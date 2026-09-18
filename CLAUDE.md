# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A "repo-to-wiki" service: given a GitHub repo URL, it clones/ingests the repo, builds a code graph + vector index, generates a Wiki (structure + content), and answers questions about the repo via two chat modes (plain RAG and a tool-using Agent). Two independent apps:

- `docker/` — Python/FastAPI backend (ingestion, wiki generation, RAG, Agent). Despite the name, it's a normal Python project; `docker/` is also the Docker build context.
- `frontend/` — Next.js 16 / React 19 app, but routing is actually client-side React Router mounted through a Next.js catch-all page (see Frontend architecture below).

## Running things

Backend (run from `docker/`, since `api.py` loads env relative to CWD):
```bash
cd docker
pip install -r requirements.txt
python scripts/api.py          # serves on :8000, loads ../../.env.local (repo-root .env.local)
```
There is no framework test runner (no pytest config) — `docker/test_faiss.py` and `docker/test_rag_tool.py` are standalone scripts invoked directly with `python <file>` for ad hoc checks, not a suite to run wholesale.

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

Env vars live in a repo-root `.env` / `.env.local` (not inside `docker/` or `frontend/`), covering OpenRouter/OpenAI/Qwen/DeepSeek keys, Supabase URL/key, Cloudflare R2 credentials, GitHub token, and optional Tavily/LangSmith keys. Model choices (OpenRouter model IDs per role: planner, evaluator, synthesizer, wiki structure/content, etc.) live in `docker/config/repo_config.json`, not in code — change models there.

## Backend architecture (`docker/src/`)

Reference docs already exist and are kept up to date — read them before making non-trivial changes rather than re-deriving this from source:
- `Document/AGENT_CHAT_ARCHITECTURE.md` — the Agent chat state machine, phase by phase, with file:line references and a list of known implementation issues (e.g. memory isn't actually persisted, `skip_tools` is largely dead, some SSE events aren't consumed by the frontend).
- `Document/API_DOCUMENTATION.md` — every HTTP endpoint, request/response shapes, Supabase/R2 client methods, path-normalization behavior.
- `Document/CODE_GRAPH_CONSTRUCTION.md` — how the code graph (nodes/edges/community detection) is built from source via tree-sitter.

Key structural facts worth knowing up front:

- **HTTP entry point**: `docker/scripts/api.py`. No Pydantic request models — every route does `await request.json()` then manual `.get(...)`/`400` checks. No auth; `user_id` is self-reported by the client.
- **Path resolution is recomputed, not trusted**: vector store paths and repo/graph paths stored in Supabase are never used directly. `_normalize_vector_store_path` / `_resolve_agent_paths` in `api.py` always recompute them from `VECTOR_STORE_ROOT` / `REPO_STORE_ROOT` plus the repo URL, so a DB row with a stale absolute path (e.g. from a different machine) still resolves correctly locally.
- **Two chat modes**, both backed by the same vector store/graph:
  - RAG (`POST /chat`, `/chat/stream`) — single hybrid retrieval (dense FAISS + BM25, MMR, optional HyDE) via `docker/src/core/chat.py` + `docker/src/core/retrieval.py`, then one LLM call. Supports token-level streaming (`answer_delta`).
  - Agent (`POST /agent/chat`, `/agent/chat/stream`) — a hand-rolled state machine (`docker/src/agent/graph.py`'s `AgentGraphRunner`; *not* LangGraph despite the filename) that plans, picks from 7 tools, runs a reflect/evaluate loop (up to `max_iterations`, default 5), and only then synthesizes. Only stage-level SSE events, no token streaming. See the architecture doc for the full phase breakdown before touching this file.
- **Tools** (`docker/src/agent/tools/`): `rag_search`, `code_graph`, `file_read`, `repo_map`, `grep_search`, `lsp_resolve`, `web_search` (`repo_map` is `RepoMapTool`, defined inside `file_tool.py` alongside `FileReadTool`, not its own file). Each implements `execute(...) -> ContextPiece` and has its own fallback chain when its dependency (graph file, repo checkout, network) is missing — check `_execute_tool` in `graph.py` for the degradation matrix before assuming a tool is unavailable vs. silently falling back to RAG.
- **Ingestion pipeline** (`docker/src/ingestion/`): `code_graph.py` builds a language-agnostic property graph via tree-sitter (nodes = file/class/function/variable, edges = structural/call/import/inheritance); `community_engine.py` runs Leiden community detection on top of it; `docu_splitter.py`/`file_processor.py`/`embedding_utils.py`/`vector_store.py` build the FAISS + BM25 indices consumed by RAG and the Agent's `rag_search` tool.
- **Wiki generation** (`docker/src/core/wiki_pipeline.py`, `docker/src/wiki/`): async task pipeline invoked from `POST /generate`; caches on `repositories.last_updated` (≤3 days, see `docker/src/utils/wiki_cache_policy.py`) but that cache check only looks at R2 artifact URLs, not whether the vector index itself is still valid.
- **Storage**: Supabase (`docker/src/storage/supabase_client.py`) for tasks/repositories/chat history/profiles; Cloudflare R2 (`docker/src/storage/r2_client.py`) for wiki JSON artifacts, keyed `{repo_name}/{YYYYMMDD}_{task_id}/`.
- **LLM client**: `docker/src/clients/__init__.py` (`get_llm()`), all via OpenRouter (`langchain_openrouter.ChatOpenRouter`). Model-per-role mapping is in `repo_config.json` under `ai_models.models`.
- **`RepoMapper/`** is a separate, mostly standalone sub-package (own `pyproject.toml`/`requirements.txt`) for repo structure summarization; treat it as a distinct unit from `docker/src`.

## Frontend architecture (`frontend/src/`)

This is not a standard Next.js App Router app internally. Actual pages live under `frontend/src/views/` (`DashboardPage`, `WikiPage`, `HistoryPage`, `LoginPage`, `AuthCallbackPage`, ...) and are routed by React Router (`frontend/src/router/RouterApp.tsx` + `router/routes/*`). Next.js only provides a single client-rendered catch-all shell: `frontend/src/app/[...slug]/page.tsx` dynamically imports `RouterApp` with `ssr: false`. Route groups like `frontend/src/app/(dashboard)/dashboard/page.tsx` are legacy redirect shims (`router.replace('/app/dashboard')`), not real pages — don't add new routes there; add a view + a route entry under `router/routes/` instead.

`frontend/src/lib/api.ts` is the API client and defines the `AgentStreamEvent` TS union for SSE events from `/agent/chat/stream` — cross-check against `Document/AGENT_CHAT_ARCHITECTURE.md` §9 if adding/consuming a new event type, since some backend-emitted events (`subtask_parallel`, `final_result`) currently aren't in this union / aren't handled by `ChatInterface.tsx`, and one frontend-handled event (`answer_delta`) is never emitted on the Agent path.

## Known sharp edges (see docs for full detail, don't relitigate without reading them)

- Agent session memory (`SessionMemory`, `RepoFactsMemory` in `docker/src/agent/state.py`) is never persisted — it's rebuilt fresh every request and discarded at the end of `run()`. Don't assume cross-request memory works.
- `docker/requirements.txt` is missing `rope`, `duckduckgo-search`, `tavily-python`, and `tree_sitter_language_pack` — `lsp_resolve` and `web_search` degrade silently in a fresh install without these.
