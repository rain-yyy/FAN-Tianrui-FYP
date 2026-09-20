"""
Core "project input -> create task" and "task management" (list/get/cancel/
delete) flows.

Two layers:
  - API-level tests drive POST /generate, POST /task/{id}, POST /tasks,
    POST /task/{id}/cancel and DELETE /task/{id} through the real FastAPI app.
  - State-machine tests exercise src.core.task_manager.cancel_task's branches
    directly (CancelOutcome.*), using a fake in-process asyncio.Task instead
    of a real (slow, LLM-driven) generation run, so cancel/not-yet-running/
    marked-cancelled-in-db/not-found are all covered deterministically.

None of this runs the Wiki content generation to completion — that involves
LLM calls out of scope for "core flow" testing per the task at hand. Tasks
are stopped via /cancel immediately after creation instead.
"""
import asyncio
import uuid

from src.core import task_manager
from src.core.task_manager import CancelOutcome, cancel_task
from src.paths import repo_disk_dirname

FAKE_REPO_URL = "https://github.com/octocat/Hello-World"


# ---------------------------------------------------------------------------
# API-level: POST /generate, POST /task/{id}, POST /tasks
# ---------------------------------------------------------------------------

async def test_generate_missing_url_link_returns_400(api_client, unique_user_id):
    resp = await api_client.post("/generate", json={"user_id": unique_user_id})
    assert resp.status_code == 400


async def test_generate_missing_user_id_returns_400(api_client, test_repo_url):
    resp = await api_client.post("/generate", json={"url_link": test_repo_url})
    assert resp.status_code == 400


async def test_generate_creates_task_record(api_client, test_repo_url, unique_user_id, task_cleanup):
    resp = await api_client.post("/generate", json={"url_link": test_repo_url, "user_id": unique_user_id})
    assert resp.status_code == 200
    body = resp.json()
    assert "task_id" in body and body["task_id"]
    task_id = body["task_id"]
    task_cleanup.append((task_id, unique_user_id))

    get_resp = await api_client.post(f"/task/{task_id}")
    assert get_resp.status_code == 200
    task = get_resp.json()
    assert task["task_id"] == task_id
    assert task["status"] in {"pending", "processing"}
    assert repo_disk_dirname(test_repo_url).lower() in task["repo_url"].lower()

    # stop the background generation now that we've asserted the creation shape
    await api_client.post(f"/task/{task_id}/cancel")


async def test_get_task_not_found_returns_404(api_client):
    resp = await api_client.post(f"/task/does-not-exist-{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_list_tasks_missing_user_id_returns_400(api_client):
    resp = await api_client.post("/tasks", json={})
    assert resp.status_code == 400


async def test_list_tasks_includes_created_task(api_client, test_repo_url, unique_user_id, task_cleanup):
    create_resp = await api_client.post("/generate", json={"url_link": test_repo_url, "user_id": unique_user_id})
    task_id = create_resp.json()["task_id"]
    task_cleanup.append((task_id, unique_user_id))
    await api_client.post(f"/task/{task_id}/cancel")

    list_resp = await api_client.post("/tasks", json={"user_id": unique_user_id})
    assert list_resp.status_code == 200
    task_ids = {t["task_id"] for t in list_resp.json()}
    assert task_id in task_ids


# ---------------------------------------------------------------------------
# API-level: POST /task/{id}/cancel
# ---------------------------------------------------------------------------

async def test_cancel_immediately_after_create_marks_task_failed_cancelled(
    api_client, test_repo_url, unique_user_id, task_cleanup
):
    """Assumes test_repo_url doesn't already have a fresh (<=3 day) cached
    wiki in `repositories` for this exact normalized URL — a cache hit lets
    execute_generation_task finish (status "cached") before this cancel call
    lands, which would make cancel 404 instead of succeeding. If this repo
    was already fully generated recently through the real app, pick a
    different repo or clear its `repositories` row first."""
    create_resp = await api_client.post("/generate", json={"url_link": test_repo_url, "user_id": unique_user_id})
    task_id = create_resp.json()["task_id"]
    task_cleanup.append((task_id, unique_user_id))

    cancel_resp = await api_client.post(f"/task/{task_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["success"] is True

    final_status = None
    for _ in range(20):
        info = (await api_client.post(f"/task/{task_id}")).json()
        final_status = info["status"]
        if final_status == "failed":
            assert "cancel" in (info.get("error") or "").lower()
            break
        await asyncio.sleep(0.5)
    assert final_status == "failed"


async def test_cancel_unknown_task_returns_404(api_client):
    resp = await api_client.post(f"/task/does-not-exist-{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API-level: DELETE /task/{id}
# ---------------------------------------------------------------------------

async def test_delete_task_removes_record(api_client, test_repo_url, unique_user_id):
    create_resp = await api_client.post("/generate", json={"url_link": test_repo_url, "user_id": unique_user_id})
    task_id = create_resp.json()["task_id"]
    await api_client.post(f"/task/{task_id}/cancel")

    delete_resp = await api_client.delete(f"/task/{task_id}", params={"user_id": unique_user_id})
    assert delete_resp.status_code == 200
    assert delete_resp.json()["success"] is True

    get_resp = await api_client.post(f"/task/{task_id}")
    assert get_resp.status_code == 404


async def test_delete_task_not_found_returns_404(api_client, unique_user_id):
    resp = await api_client.delete(f"/task/does-not-exist-{uuid.uuid4()}", params={"user_id": unique_user_id})
    assert resp.status_code == 404


async def test_delete_task_wrong_user_returns_404(api_client, test_repo_url, unique_user_id, task_cleanup):
    create_resp = await api_client.post("/generate", json={"url_link": test_repo_url, "user_id": unique_user_id})
    task_id = create_resp.json()["task_id"]
    task_cleanup.append((task_id, unique_user_id))
    await api_client.post(f"/task/{task_id}/cancel")

    other_user_id = str(uuid.uuid4())
    resp = await api_client.delete(f"/task/{task_id}", params={"user_id": other_user_id})
    assert resp.status_code == 404


async def test_delete_task_missing_user_id_is_rejected(api_client, unique_user_id):
    resp = await api_client.delete(f"/task/does-not-exist-{uuid.uuid4()}")
    assert resp.status_code == 422  # user_id is a required query param


# ---------------------------------------------------------------------------
# State-machine level: src.core.task_manager.cancel_task branches
# ---------------------------------------------------------------------------

def test_cancel_task_not_yet_running(supabase_client, unique_user_id, task_cleanup):
    task_id = f"pytest-{uuid.uuid4()}"
    assert supabase_client.create_task(unique_user_id, task_id, FAKE_REPO_URL)
    task_cleanup.append((task_id, unique_user_id))

    outcome, error = cancel_task(task_id, supabase_client)
    assert outcome == CancelOutcome.NOT_YET_RUNNING
    assert error is None


async def test_cancel_task_cancels_registered_in_process_task(supabase_client, unique_user_id, task_cleanup):
    task_id = f"pytest-{uuid.uuid4()}"
    supabase_client.create_task(unique_user_id, task_id, FAKE_REPO_URL)
    task_cleanup.append((task_id, unique_user_id))
    supabase_client.update_task_status(task_id, "processing")

    fake_task = asyncio.create_task(asyncio.sleep(30))
    task_manager.register_task(task_id, fake_task)

    outcome, error = cancel_task(task_id, supabase_client)
    assert outcome == CancelOutcome.CANCELLED
    assert error is None

    await asyncio.wait([fake_task])  # let the cancellation actually propagate into fake_task
    assert fake_task.cancelled()

    row = supabase_client.get_task(task_id)
    assert row.status == "failed"
    assert "cancel" in (row.error or "").lower()


def test_cancel_task_marked_cancelled_in_db_without_in_process_registration(
    supabase_client, unique_user_id, task_cleanup
):
    """Simulates a server restart: DB still says `processing`, but nothing
    is registered in this process's in-memory _running_tasks table."""
    task_id = f"pytest-{uuid.uuid4()}"
    supabase_client.create_task(unique_user_id, task_id, FAKE_REPO_URL)
    task_cleanup.append((task_id, unique_user_id))
    supabase_client.update_task_status(task_id, "processing")

    outcome, error = cancel_task(task_id, supabase_client)
    assert outcome == CancelOutcome.MARKED_CANCELLED_IN_DB
    assert error is None

    row = supabase_client.get_task(task_id)
    assert row.status == "failed"
    assert "cancel" in (row.error or "").lower()


def test_cancel_task_not_found(supabase_client):
    outcome, error = cancel_task(f"does-not-exist-{uuid.uuid4()}", supabase_client)
    assert outcome == CancelOutcome.NOT_FOUND
    assert error is None


def test_delete_task_not_found_result(supabase_client, unique_user_id):
    from src.storage.supabase_client import DeleteTaskResult

    result = supabase_client.delete_task(f"does-not-exist-{uuid.uuid4()}", unique_user_id)
    assert result == DeleteTaskResult.NOT_FOUND
