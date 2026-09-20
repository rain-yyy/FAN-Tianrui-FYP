"""
Manual smoke test for the unified chat/agent backend (docker/src/chat/).

Hits POST /chat, POST /chat/stream, and /chat/history* against the real
FastAPI app -- real OpenRouter, Qdrant and Supabase calls, no mocking.
Deliberately NOT named test_*.py / *_test.py so plain `pytest` never
auto-runs it (it costs real LLM tokens each run).

Run from docker/:

    uv run python tests/manual/chat_agent_smoke.py
    uv run python tests/manual/chat_agent_smoke.py --base-url http://localhost:8000

Env:
    TEST_USER_ID       - required; a real profiles/auth.users id (same var
                          docker/tests/conftest.py uses)
    TEST_CHAT_REPO_URL - optional; skip auto-discovery and use this repo_url
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

DOCKER_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = DOCKER_ROOT.parent
if str(DOCKER_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCKER_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(REPO_ROOT / ".env")

import httpx

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"
_results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    _results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))


async def build_client(base_url: str | None) -> tuple[httpx.AsyncClient, bool]:
    if base_url:
        return httpx.AsyncClient(base_url=base_url, timeout=120.0), True
    from scripts.api import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=120.0), False


async def resolve_repo_url(client: httpx.AsyncClient, user_id: str) -> str | None:
    override = os.getenv("TEST_CHAT_REPO_URL")
    if override:
        return override

    resp = await client.post("/dashboard/repos", json={"user_id": user_id})
    if resp.status_code == 200:
        for repo in resp.json().get("repos", []):
            if repo.get("vector_store_path"):
                return repo["repo_url"]

    resp = await client.get("/chat/repos")
    if resp.status_code == 200:
        for repo in resp.json().get("repos", []):
            if repo.get("vector_store_path"):
                return repo.get("repo_url")
    return None


def parse_sse(raw_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw_text.split("\n\n"):
        event_name, data_line = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_line = line[len("data:"):].strip()
        if event_name and data_line is not None:
            try:
                events.append((event_name, json.loads(data_line)))
            except json.JSONDecodeError:
                events.append((event_name, {"_raw": data_line}))
    return events


async def scenario_basic_qa(client, repo_url, user_id) -> str | None:
    payload = {"question": "What does this repository do, in one or two sentences?",
               "repo_url": repo_url, "user_id": user_id}
    resp = await client.post("/chat", json=payload)
    if resp.status_code != 200:
        record(FAIL, "basic_qa", f"HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    data = resp.json()
    ok = bool(data.get("answer")) and bool(data.get("chat_id")) and data.get("iterations", 0) >= 1
    record(PASS if ok else FAIL, "basic_qa",
           f"chat_id={data.get('chat_id')} iterations={data.get('iterations')} answer[:80]={data.get('answer', '')[:80]!r}")
    return data.get("chat_id") if ok else None


async def scenario_tool_use(client, repo_url, user_id) -> None:
    payload = {"question": "Search the codebase for how the chat agent's tools are assembled per session, "
                            "and name the function responsible.",
               "repo_url": repo_url, "user_id": user_id}
    resp = await client.post("/chat", json=payload)
    if resp.status_code != 200:
        record(FAIL, "tool_use", f"HTTP {resp.status_code}: {resp.text[:300]}")
        return
    data = resp.json()
    trajectory = data.get("tool_trajectory", [])
    errors = [t for t in trajectory if t.get("status") != "success"]
    record(PASS if trajectory else FAIL, "tool_use",
           f"tools={[t.get('tool') for t in trajectory]} errors={errors}")


async def scenario_streaming(client, repo_url, user_id) -> None:
    payload = {"question": "Briefly, what testing already exists for the chat agent's LangGraph routing?",
               "repo_url": repo_url, "user_id": user_id}
    try:
        async with client.stream("POST", "/chat/stream", json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                record(FAIL, "streaming", f"HTTP {resp.status_code}: {body[:300]!r}")
                return
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
    except Exception as e:
        record(FAIL, "streaming", f"exception: {e!r}")
        return

    events = parse_sse(buffer)
    names = [e[0] for e in events]
    answer_done = next((data for name, data in events if name == "answer_done"), None)
    deltas = "".join(data.get("delta", "") for name, data in events if name == "answer_token")
    ok = bool(names) and names[0] == "turn_start" and names[-1] == "complete" \
        and answer_done is not None and bool(answer_done.get("answer"))
    record(PASS if ok else FAIL, "streaming", f"event_sequence={names}")
    if answer_done:
        answer = answer_done.get("answer", "")
        consistent = not deltas.strip() or deltas.strip() in answer or answer in deltas
        record(INFO, "streaming.delta_consistency",
               f"concatenated_deltas_len={len(deltas)} matches_answer={consistent}")


async def scenario_multi_turn(client, repo_url, user_id) -> None:
    first = {"question": "In one sentence, what is this repository's purpose?",
             "repo_url": repo_url, "user_id": user_id}
    resp1 = await client.post("/chat", json=first)
    if resp1.status_code != 200:
        record(FAIL, "multi_turn", f"turn1 HTTP {resp1.status_code}: {resp1.text[:300]}")
        return
    chat_id = resp1.json().get("chat_id")

    followup = {"question": "Summarize what you just told me in one short sentence.",
                "repo_url": repo_url, "user_id": user_id, "chat_id": chat_id}
    resp2 = await client.post("/chat", json=followup)
    if resp2.status_code != 200:
        record(FAIL, "multi_turn", f"turn2 HTTP {resp2.status_code}: {resp2.text[:300]}")
        return
    data2 = resp2.json()
    ok = bool(data2.get("answer")) and data2.get("chat_id") == chat_id
    record(PASS if ok else FAIL, "multi_turn",
           f"chat_id={chat_id} turn2_answer[:120]={data2.get('answer', '')[:120]!r} (judge recall quality manually)")


async def scenario_error_paths(client, repo_url, user_id) -> None:
    # Omitted required field: Pydantic rejects the body before the handler runs -> 422.
    resp = await client.post("/chat", json={"repo_url": repo_url, "user_id": user_id})
    record(PASS if resp.status_code == 422 else FAIL, "error_omitted_question", f"HTTP {resp.status_code}")

    # Empty-string value: passes Pydantic, hits chat_session.py's own "Missing ..." check -> 400.
    resp = await client.post("/chat", json={"question": "", "repo_url": repo_url, "user_id": user_id})
    record(PASS if resp.status_code == 400 else FAIL, "error_empty_question", f"HTTP {resp.status_code}")

    resp = await client.post("/chat", json={"question": "hi", "repo_url": "", "user_id": user_id})
    record(PASS if resp.status_code == 400 else FAIL, "error_empty_repo_url", f"HTTP {resp.status_code}")

    resp = await client.post("/chat", json={"question": "hi", "repo_url": repo_url, "user_id": ""})
    record(PASS if resp.status_code == 400 else FAIL, "error_empty_user_id", f"HTTP {resp.status_code}")

    resp = await client.post("/chat", json={
        "question": "hi", "user_id": user_id,
        "repo_url": "https://github.com/this-org-does-not-exist-12345/this-repo-does-not-exist-67890.git",
    })
    record(PASS if resp.status_code == 404 else FAIL, "error_unindexed_repo",
           f"HTTP {resp.status_code}: {resp.text[:200]}")


async def scenario_history_roundtrip(client, chat_ids: list[str], user_id: str) -> None:
    chat_ids = [c for c in chat_ids if c]
    if not chat_ids:
        record(FAIL, "history_roundtrip", "no chat_id available from earlier scenarios")
        return

    resp = await client.get("/chat/history", params={"user_id": user_id})
    history_ids = {row.get("chat_id") or row.get("id") for row in resp.json().get("history", [])} \
        if resp.status_code == 200 else set()
    ok = resp.status_code == 200 and all(cid in history_ids for cid in chat_ids)
    record(PASS if ok else FAIL, "history_list",
           f"HTTP {resp.status_code} found={len(history_ids)} expected_subset={chat_ids}")

    target = chat_ids[0]
    resp = await client.get(f"/chat/messages/{target}")
    messages = resp.json().get("messages", []) if resp.status_code == 200 else []
    ok = resp.status_code == 200 and len(messages) >= 2 and messages[0].get("role") == "user"
    record(PASS if ok else FAIL, "history_messages",
           f"HTTP {resp.status_code} count={len(messages)} roles={[m.get('role') for m in messages]}")

    resp = await client.delete(f"/chat/history/{target}", params={"user_id": "not-" + user_id})
    record(PASS if resp.status_code == 404 else FAIL, "history_delete_wrong_user", f"HTTP {resp.status_code}")

    resp = await client.delete(f"/chat/history/{target}", params={"user_id": user_id})
    record(PASS if resp.status_code == 200 else FAIL, "history_delete_correct_user", f"HTTP {resp.status_code}")

    resp = await client.get(f"/chat/messages/{target}")
    ok = resp.status_code == 200 and resp.json().get("messages") == []
    record(PASS if ok else FAIL, "history_delete_verified",
           f"HTTP {resp.status_code} messages_after_delete={resp.json().get('messages')}")


async def scenario_web_search_info(client, repo_url, user_id) -> None:
    payload = {"question": "What is the latest stable version of Python as of today, per public sources?",
               "repo_url": repo_url, "user_id": user_id}
    resp = await client.post("/chat", json=payload)
    if resp.status_code != 200:
        record(INFO, "web_search_probe", f"HTTP {resp.status_code} (informational scenario, not a failure)")
        return
    tools_used = [t.get("tool") for t in resp.json().get("tool_trajectory", [])]
    record(INFO, "web_search_probe", f"tools_used={tools_used} web_search_used={'web_search' in tools_used}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=None,
                         help="Hit a real running server instead of the in-process ASGI app, e.g. http://localhost:8000")
    args = parser.parse_args()

    user_id = os.getenv("TEST_USER_ID")
    if not user_id:
        print("TEST_USER_ID is not set (check repo-root .env.local) -- chat needs a real profiles/auth.users id.")
        return 2

    client, is_real_server = await build_client(args.base_url)
    async with client:
        mode = f"real server at {args.base_url}" if is_real_server else "in-process ASGI (no live server needed)"
        print(f"=== chat/agent smoke test -- {mode} ===\n")

        repo_url = await resolve_repo_url(client, user_id)
        if not repo_url:
            print("No already-indexed repo found for this user (checked /dashboard/repos and /chat/repos).")
            print("Run POST /generate for a repo first, or set TEST_CHAT_REPO_URL to an indexed repo_url.")
            return 2
        print(f"Using repo_url={repo_url!r} user_id={user_id!r}\n")

        chat_ids = [await scenario_basic_qa(client, repo_url, user_id)]
        await scenario_tool_use(client, repo_url, user_id)
        await scenario_streaming(client, repo_url, user_id)
        await scenario_multi_turn(client, repo_url, user_id)
        await scenario_error_paths(client, repo_url, user_id)
        await scenario_history_roundtrip(client, chat_ids, user_id)
        await scenario_web_search_info(client, repo_url, user_id)

    print("\n=== summary ===")
    passed = sum(1 for s, _, _ in _results if s == PASS)
    failed = sum(1 for s, _, _ in _results if s == FAIL)
    info = sum(1 for s, _, _ in _results if s == INFO)
    print(f"{passed} passed, {failed} failed, {info} info")
    if failed:
        print("\nFailures:")
        for s, name, detail in _results:
            if s == FAIL:
                print(f"  - {name}: {detail}")

    print("\n=== manual follow-up checklist (not automated) ===")
    print("- Check whether chat_history.session_summary / summary_up_to_created_at columns exist yet in")
    print("  live Supabase (docker/sql/2026_chat_memory.sql) -- informational, code degrades gracefully either way.")
    print("- Spot-check the chat_messages/chat_history rows created by this run in Supabase for deeper detail.")
    print("- frontend/src/lib/api.ts still targets the old /agent/chat* endpoints per CLAUDE.md -- this script")
    print("  only validates the backend directly, not the browser chat UI.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
