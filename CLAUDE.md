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

## Frontend architecture (`frontend/src/`)

This is not a standard Next.js App Router app internally. Actual pages live under `frontend/src/views/` (`DashboardPage`, `WikiPage`, `HistoryPage`, `LoginPage`, `AuthCallbackPage`, ...) and are routed by React Router (`frontend/src/router/RouterApp.tsx` + `router/routes/*`). Next.js only provides a single client-rendered catch-all shell: `frontend/src/app/[...slug]/page.tsx` dynamically imports `RouterApp` with `ssr: false`. Route groups like `frontend/src/app/(dashboard)/dashboard/page.tsx` are legacy redirect shims (`router.replace('/app/dashboard')`), not real pages — don't add new routes there; add a view + a route entry under `router/routes/` instead.

`frontend/src/lib/api.ts` is the API client. **It already targets the new unified backend** — `askQuestionStream` calls `/chat/stream` and parses the current `ChatStreamEvent` vocabulary (`turn_start`/`iteration_start`/`tool_call_start`/`tool_call_result`/`answer_token`/`answer_done`/`complete`/`error`), not the old two-vocabulary contract. `Document/API_DOCUMENTATION.md`'s §0 "Frontend integration gap" section still describes the old (now-fixed) mismatch — treat that section as stale and re-verify against the live `api.ts` before relying on it.

## Backend notes

Backend architecture and known sharp edges live in `docker/CLAUDE.md` (loaded when working under `docker/`).
