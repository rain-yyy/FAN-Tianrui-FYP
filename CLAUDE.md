# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Code Style
- Python code follows PEP 8, checked and formatted by tuff (config in pyproject.toml)
- Docstrings are not required; code just needs to satisfy PEP 8 and pass the ruff checks configured in `docker/pyproject.toml`
- Import order: stblib -> third-patry -> local, sorted automatically by buff's isort rules

# Workflow
- After editing Python files, run (from `docker/`): `uv run ruff check --fix . && uv run ruff format .`
- Make sure `ruff check . ` passes before commiting

# Testing
Smoke tests (fast, no real external calls; slow tests only via `pytest -m slow`):
- Backend: `cd docker && uv run pytest` for unit tests, then start `uv run python scripts/api.py` and check `curl localhost:8000/health` returns OK.
- Frontend: `cd frontend && npm run lint && npm run build` must both pass; then `npm run dev` and open `http://localhost:3000` to confirm the page loads.

## What this repo is

A "repo-to-wiki" service: given a GitHub repo URL, it clones/ingests the repo, builds a code graph + vector index, generates a Wiki (structure + content), and answers questions about the repo via a single agent-first chat backend (a tool-using LangGraph agent — see Backend architecture below for the recent rewrite that unified what used to be a separate plain-RAG mode and Agent mode). Two independent apps:

- `docker/` — Python/FastAPI backend (ingestion, wiki generation, chat agent). Despite the name, it's a normal Python project; `docker/` is also the Docker build context.
- `frontend/` — Next.js 16 / React 19 app, but routing is actually client-side React Router mounted through a Next.js catch-all page (see Frontend architecture below).

## Running things

Backend (run from `docker/`, since `api.py` loads env relative to CWD):
```bash
cd docker
uv sync
uv run python scripts/api.py          # serves on :8000, loads ../../.env.local (repo-root .env.local)
```
Automated tests now exist: `docker/tests/` (pytest, config in `docker/pytest.ini`, run with `pytest` from `docker/`). Tests marked `slow` hit real git clone + real embeddings + real Qdrant/Supabase and only run explicitly (`pytest -m slow`).

Docker (context is `docker/`, so build/run from inside that directory or with `-f`):
```bash
docker compose -f docker/docker-compose.yml up --build
```

Frontend (run from `frontend/`):
```bash
cd frontend
npm run dev      # next dev
npm run build
npm run lint      # eslint
```

Env vars live in a repo-root `.env` / `.env.local` (not inside `docker/` or `frontend/`), covering OpenRouter/OpenAI/Qwen/DeepSeek keys, Supabase URL/key, Cloudflare R2 credentials, GitHub token, and optional Tavily/LangSmith keys. Model choices (OpenRouter model IDs per role: planner, evaluator, synthesizer, wiki structure/content, etc.) live in `docker/config/repo_config.json`, not in code — change models there. Most other previously-hardcoded tuning constants (chunk sizes, batch sizes, RAG-retry delays, wiki concurrency) have also been moved into `repo_config.json` and are read through named getters in `docker/src/config.py`.

## Backend architecture (`docker/src/`)

`Document/API_DOCUMENTATION.md` is the maintained reference for every HTTP endpoint (request/response shapes, SSE event vocabulary) and the Supabase schema — read it before touching routers, the chat backend, or anything the frontend depends on. `Document/BACKEND_CLEANUP_PLAN.md` and `Document/QDRANT_MIGRATION_PLAN.md` are now closed-out historical planning docs (both fully executed) — skim them only for *why* the current shape looks the way it does, not as a to-do list.

Key structural facts worth knowing up front:

- **HTTP entry point**: `docker/scripts/api.py` is a thin app assembly that mounts routers from `docker/src/api/routers/` (`health`, `tasks`, `repos`, `files`, `chat`); it no longer contains route logic itself. There is no `agent` router any more (see unified chat below). Most routes still have no Pydantic request models — `tasks`/`repos`/`files` do `await request.json()` then manual `.get(...)`/`400` checks — but the `chat` router now does (`ChatTurnRequest`/`ChatTurnResponse` in `docker/src/chat/models.py`). No auth; `user_id` is self-reported by the client.
- **Path resolution is centralized and recomputed, not trusted**: `PROJECT_ROOT`/`VECTOR_STORE_ROOT`/`REPO_STORE_ROOT` are single-sourced in `docker/src/paths.py`. Vector store and repo/graph paths stored in Supabase are never used directly — `docker/src/core/path_resolver.py` always recomputes them from those roots plus the repo URL, so a DB row with a stale absolute path (e.g. from a different machine) still resolves correctly locally.
- **Vector store backend is Qdrant** (migrated off FAISS) — dense vectors via `fastembed`, plus a BM25 sparse index, combined in hybrid retrieval. Any reference to FAISS in code comments is stale wording, not live behavior.
- **One unified, agent-first chat endpoint** (`POST /chat`, `/chat/stream`) — the old RAG-only (`core/chat.py`) vs. Agent (`docker/src/agent/`, `POST /agent/chat*`) split is gone; both were deleted and replaced by `docker/src/chat/` (`graph.py`, `service.py`, `state.py`, `models.py`, `memory.py`, `prompts.py`, `events.py`, `tools/`). It's real LangGraph now (`StateGraph` in `chat/graph.py`), not the hand-rolled state machine the old `agent/graph.py` filename implied. A simple two-node `agent ⇄ tools` loop driven by the model's own native tool-calling: no intent classifier, no DIRECT/LIGHT/DEEP routing, no anchors/evidence-cards/confidence scoring. A hard cap on tool-calling rounds (`chat.max_tool_iterations` in `repo_config.json`, default 6) routes to a `final_answer` node that forces a real text answer instead of ever silently returning `""`. SSE vocabulary: `turn_start`, `iteration_start`, `tool_call_start`, `tool_call_result`, `answer_token`, `answer_done`, `complete`, `error` — this replaced *both* the old RAG vocabulary (`retrieval_start`/`hyde_generated`/`retrieval_done`/`answer_delta`/`answer_done`) and the old Agent vocabulary (`planning`/`subtask_parallel`/`tool_call`/`evaluation`/`synthesis`). Full request/response/event shapes are in `Document/API_DOCUMENTATION.md`.
  - **Tools** (`docker/src/chat/tools/`): `rag_search`, `code_graph`, `file_read`, `repo_map`, `grep_search`, `web_search` — assembled per-turn by `build_tools_for_session()`. `lsp_resolve` is gone (was the old Agent's `rope`-dependent tool).
  - **Memory is server-authoritative**, not client-supplied: the client no longer sends `conversation_history`; every turn `docker/src/chat/memory.py` rebuilds the message window from Supabase `chat_messages`, token-budgets it, and prepends a rolling summary (`chat_history.session_summary`) once a session passes `chat.history_summary_trigger_messages` (default 20) unsummarized messages. **This depends on `chat_history.session_summary`/`summary_up_to_created_at` columns that are not yet applied to the live Supabase schema** — see `docker/sql/2026_chat_memory.sql` and the sharp edge below.
- **Ingestion pipeline** (`docker/src/ingestion/`): `code_graph.py` builds a language-agnostic property graph via tree-sitter (nodes = file/class/function/variable, edges = structural/call/import/inheritance) and also ranks important symbols by PageRank directly (the old standalone `RepoMapper` library was removed; this replaced it); `community_engine.py` runs Leiden community detection on top of it; the rest builds the Qdrant + BM25 indices consumed by the chat agent's `rag_search` tool.
- **Wiki generation** (`docker/src/core/wiki_pipeline.py`, `docker/src/wiki/`): async task pipeline invoked from `POST /generate`; caches on `repositories.last_updated` (≤3 days) but that cache check only looks at R2 artifact URLs, not whether the vector index itself is still valid.
- **Storage**: Supabase (`docker/src/storage/supabase_client.py`) for tasks/repositories/chat history/profiles; Cloudflare R2 (`docker/src/storage/r2_client.py`) for wiki JSON artifacts, keyed `{repo_name}/{YYYYMMDD}_{task_id}/`.
- **LLM client**: `docker/src/clients/__init__.py` (`get_llm()`), all via OpenRouter. Model-per-role mapping is in `repo_config.json` under `ai_models.models` — chat now has its own roles, `chat_agent` and `chat_session_compressor` (used for background summarization).
- **Debug artifacts are opt-in**: raw-response/chunk debug dumps are gated behind `debug.*` flags in `repo_config.json`, off by default — don't expect debug files on disk unless those are turned on.

## Frontend architecture (`frontend/src/`)

This is not a standard Next.js App Router app internally. Actual pages live under `frontend/src/views/` (`DashboardPage`, `WikiPage`, `HistoryPage`, `LoginPage`, `AuthCallbackPage`, ...) and are routed by React Router (`frontend/src/router/RouterApp.tsx` + `router/routes/*`). Next.js only provides a single client-rendered catch-all shell: `frontend/src/app/[...slug]/page.tsx` dynamically imports `RouterApp` with `ssr: false`. Route groups like `frontend/src/app/(dashboard)/dashboard/page.tsx` are legacy redirect shims (`router.replace('/app/dashboard')`), not real pages — don't add new routes there; add a view + a route entry under `router/routes/` instead.

`frontend/src/lib/api.ts` is the API client. **It already targets the new unified backend** — `askQuestionStream` calls `/chat/stream` and parses the current `ChatStreamEvent` vocabulary (`turn_start`/`iteration_start`/`tool_call_start`/`tool_call_result`/`answer_token`/`answer_done`/`complete`/`error`), not the old two-vocabulary contract. `Document/API_DOCUMENTATION.md`'s §0 "Frontend integration gap" section still describes the old (now-fixed) mismatch — treat that section as stale and re-verify against the live `api.ts` before relying on it.

## Known sharp edges (don't relitigate without checking source first)

- **The `chat_history.session_summary`/`summary_up_to_created_at` columns the new memory design (`docker/src/chat/memory.py`) depends on are not present in the live Supabase schema yet.** The migration (`docker/sql/2026_chat_memory.sql`) has not been applied (confirmed via `mcp__supabase__list_migrations` — only two unrelated `repositories.repo_name` migrations are recorded); the SQL to run is in `Document/API_DOCUMENTATION.md` §0. Until it's applied, `update_chat_session_summary`/`get_chat_session` calls will silently no-op or return rows without those keys (the Supabase client swallows the exception) and summarization will never actually persist — it won't crash requests. (The column was originally specified as `summary_up_to_message_id bigint REFERENCES chat_messages(id)`, which would have failed at apply time since `chat_messages.id` is `uuid` — fixed to a plain `summary_up_to_created_at timestamptz` watermark, with `chat/memory.py` and `supabase_client.py` updated to match.)
- `repo_memory` (durable cross-session repo facts) was removed from the chat agent along with its extractor/planner/verifier prompts and model roles. Only the table definition remains in `docker/sql/2026_chat_memory.sql`; nothing reads or writes it, and it is not in the live DB.
- Python deps are managed only by uv: `docker/pyproject.toml` + `docker/uv.lock` (no `requirements.txt`; Dockerfile uses `uv sync --frozen`). `rope` and `tavily-python` are absent, but `rope` no longer matters — the `lsp_resolve` tool that needed it was deleted in the chat rewrite. `tavily-python` only matters if `web_search.provider` in `repo_config.json` is switched from the default `duckduckgo`.
- `Document/BACKEND_CLEANUP_PLAN.md`'s tracks are now fully complete (see the doc's own status header) — don't go looking for unfinished items there.
