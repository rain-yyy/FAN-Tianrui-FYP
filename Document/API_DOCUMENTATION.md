# API Documentation

*Reference for `docker/src/api/routers/*` (HTTP surface), `docker/src/chat/` (the chat agent behind `/chat`), and the Supabase schema it all reads/writes. Written for frontend work against this backend — every shape here was read directly from the current source and the live Supabase project, not copied from an older doc.*

*Last verified: 2026-09-19, against the working tree (includes uncommitted changes — see "Uncommitted state" below) and the live Supabase project (via `mcp__supabase__list_tables`/`list_migrations`).*

---

## 0. Things to know before reading further

### Uncommitted state

`git status` currently shows `docker/src/agent/`, `docker/src/core/chat.py`, and `docker/src/api/routers/agent.py` as **deleted**, and `docker/src/chat/` (the module this doc describes) as **untracked**. This whole chat-backend rewrite — replacing the old RAG-only + Agent-only split with one unified agent-first endpoint — is sitting in the working tree, not committed. This doc describes what's on disk now, which is what the frontend will actually talk to once this lands, but be aware `git log`/`git blame` on `docker/src/chat/` currently shows nothing.

### Frontend integration gap

`frontend/src/lib/api.ts` was written against the **old** backend and has not been updated. Before or while doing frontend work, expect to fix:

| Frontend today (`frontend/src/lib/api.ts`) | Current backend reality |
|---|---|
| Calls `POST /agent/chat` and `POST /agent/chat/stream` | Both routes are **gone**. There is one endpoint: `POST /chat` / `POST /chat/stream`. |
| `askQuestionStream` parses `RagStreamEvent` (`retrieval_start`/`hyde_generated`/`retrieval_done`/`answer_delta`/`answer_done`/`complete`/`error`) against `/chat/stream` | `/chat/stream` now emits a different vocabulary: `turn_start`/`iteration_start`/`tool_call_start`/`tool_call_result`/`answer_token`/`answer_done`/`complete`/`error` (§3.6). |
| `askAgentQuestionStream` parses `AgentStreamEvent` (`planning`/`tool_call`/`evaluation`/`synthesis`/`answer_delta`/`complete`/`error`) against `/agent/chat/stream` | That vocabulary and that route no longer exist at all. |
| `ChatRequest` includes `conversation_history?: ChatMessage[]` sent by the client | The backend no longer accepts or reads this — history is rebuilt server-side from `chat_messages` every turn (§3.5, §5.3). Safe to stop sending it. |
| `AgentChatResponse` expects `mermaid`, `confidence`, `confidence_level`, `anchors_count`, `evidence_count`, `caveats` | None of these exist on the new `ChatTurnResponse` (§3.5) — the new agent doesn't compute a confidence score or anchors/evidence cards at all. |
| `getTaskStatus('/task/:id')` expects `{ task: TaskStatusResponse }` | The route returns a `TaskRecord` object **directly** (no `task` wrapper) — see §3.2. |
| `getTasks('/tasks')` expects `{ tasks: TaskStatusResponse[] }` | The route returns a **raw array** of `TaskRecord`, no `tasks` wrapper — see §3.2. |
| No `ChatMode` toggle needed | `ChatMode`/`'rag' | 'agent'` in `api.ts` is vestigial — there's only one mode now. |

The last two rows (task endpoints) are pre-existing mismatches independent of the chat rewrite — worth a quick manual check against a running backend before assuming either side is "right."

### Database migration not yet applied

The new chat memory design (§5.3, §3.5) depends on two `chat_history` columns and reads/writes them via `SupabaseClient.get_chat_session`/`update_chat_session_summary`. **These columns do not exist in the live Supabase schema yet** (confirmed via `mcp__supabase__list_tables`) and the migration has not been run (confirmed via `mcp__supabase__list_migrations` — only two unrelated `repositories.repo_name` migrations are recorded). Until it's applied, session-summary reads/writes fail inside a try/except and are silently swallowed — summarization quietly never persists, it doesn't crash requests.

The migration file (`docker/sql/2026_chat_memory.sql`) originally had a bug — `summary_up_to_message_id bigint REFERENCES chat_messages(id)`, but `chat_messages.id` is `uuid`, not `bigint`, so that FK would have failed to create. **This has been fixed**: the cutoff column is now `summary_up_to_created_at timestamptz` (no FK — it's a plain watermark, not a foreign key), and `docker/src/chat/memory.py`/`supabase_client.py` were updated to match (cutoff comparison is now a `created_at` string comparison, not an `id` comparison). Run this in the Supabase SQL editor to apply it:
```sql
ALTER TABLE chat_history
  ADD COLUMN IF NOT EXISTS session_summary text,
  ADD COLUMN IF NOT EXISTS summary_up_to_created_at timestamptz;

CREATE TABLE IF NOT EXISTS repo_memory (
  repo_url text PRIMARY KEY,
  facts jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);
```
(Full file: `docker/sql/2026_chat_memory.sql` — the `repo_memory` table is included for forward-compatibility but has no read/write code yet, see §8.)

### No auth on the API itself

Every endpoint below trusts a client-supplied `user_id` — there's no server-side session/token check. Auth (Supabase Auth) happens only in the frontend for gating UI/routes; the FastAPI backend does not verify the caller is who they claim to be.

---

## 1. Base setup

- Base URL: `http://localhost:8000` in dev (`docker/scripts/api.py`, `uvicorn.run(app, host="0.0.0.0", port=8000)`).
- CORS: wide open (`allow_origins=["*"]`, all methods/headers) — dev-only posture, not narrowed for any deployed origin.
- Content type: all POST bodies are JSON. Most routes take a raw `Request` and do `await request.json()` + manual `.get(...)` + `400` on missing fields (no Pydantic validation, no OpenAPI-visible request schema) — **except** `/chat` and `/chat/stream`, which use real Pydantic models (`ChatTurnRequest`) and so *do* get automatic 422 validation errors on malformed bodies, unlike every other route here.
- Routers mounted in `docker/scripts/api.py`: `health`, `tasks`, `repos`, `files`, `chat` (all under `docker/src/api/routers/`). There is no `agent` router.

---

## 2. `health` — `docker/src/api/routers/health.py`

### `GET /health`
No params. Returns `{"status": "ok"}`. Used by the frontend's `checkHealth()`.

---

## 3. `tasks` — `docker/src/api/routers/tasks.py` (wiki generation)

### 3.1 `POST /generate`
Create a wiki-generation task (async; the actual pipeline runs in the background).

Request body: `{"url_link": string, "user_id": string}` (both required, else `400`).

Response: `{"task_id": string, "message": string}`. Poll `/task/{task_id}` for progress. Internally: creates a `tasks` row (status `pending`), then fires `start_generation_task` (an in-process `asyncio.Task`, tracked so it can later be force-cancelled). Cache-hit vs. full-regeneration is decided *inside* the background task, not at request time.

### 3.2 `POST /task/{task_id}`
Fetch one task's current state. No body needed (task_id is a path param — POST is used instead of GET here for historical reasons, not semantics).

Returns a `TaskRecord` (`docker/src/storage/models.py`) **as the top-level JSON object**, not wrapped in `{"task": ...}`:
```jsonc
{
  "id": "uuid",
  "user_id": "uuid",
  "task_id": "string",
  "repo_url": "string",
  "status": "pending" | "processing" | "completed" | "cached" | "failed",
  "created_at": "ISO datetime | null",
  "last_updated": "ISO datetime | null",
  "progress": 0.0,           // coerced to float; stored as text in Supabase
  "current_step": "string | null",
  "result": { /* GenResponse shape, see 3.2.1 — {} until status is completed/cached */ },
  "error": "string | null"
}
```
`404` if the task doesn't exist; `503` if Supabase itself is unreachable.

#### 3.2.1 `result` shape (once populated)
Assembled in `docker/src/core/wiki_pipeline.py`:
```jsonc
{
  "r2_structure_url": "string | null",
  "r2_content_urls": ["string", ...] | null,
  "json_wiki": "string | null",       // local fallback path, only set if r2_structure_url is null
  "json_content": "string | null",    // local fallback path, only set if r2_content_urls is null
  "vector_store_path": "string | null",
  "repo_url": "string"
}
```
This matches the frontend's `GenResponse` type as-is — no change needed here.

### 3.3 `POST /tasks`
List all tasks for a user. Body: `{"user_id": string}` (required).

Returns a **raw JSON array** of `TaskRecord` (see 3.2), not `{"tasks": [...]}`.

### 3.4 `POST /task/{task_id}/cancel`
Cancel a task (in-memory if still running, else marks `cancelled` in Supabase directly, e.g. if the process restarted). Returns `{"success": true, "message": "..."}` on any successful outcome (running-task cancelled / DB-marked-cancelled / not-yet-running-nothing-to-do), or a `503` if a Supabase write during cancellation fails, or `404` if no task was found in either place.

### 3.5 `DELETE /task/{task_id}?user_id=...`
Delete a task row. `user_id` is a required query param (not body — this is a `DELETE`). `404` if not found, `500` on other failure, else `{"success": true}`.

---

## 4. `repos` — `docker/src/api/routers/repos.py`

### 4.1 `POST /dashboard/repos`
Body: `{"user_id": string}`. Returns `{"repos": [...]}` — the user's dashboard cards: repos in the `repositories` table with complete wiki artifacts, one per successfully-generated repo, including a `task_id` the frontend uses to link into `/wiki/:taskId`. Distinct from 4.3 below — see the docstring in `supabase_client.py` on `get_user_dashboard_repositories` vs. `get_all_indexed_repos` for the full rationale if the two ever need reconciling.

Each entry (from `DashboardRepoEntry` on the frontend, still accurate):
```jsonc
{
  "repo_url": "string",
  "task_id": "string",
  "github_short_description": "string | null",
  "description": "string | null",
  "stargazers_count": "number | null",
  "vector_store_path": "string | null",
  "last_updated": "ISO datetime | null"
}
```

### 4.2 `GET /repos/github-metadata`
No params. Returns `{"metadata": {"<repo_url>": {"repo_url", "stars", "github_short_description"}, ...}}` for every row in `repositories` that has a `repo_url`. `503` if Supabase isn't configured.

### 4.3 `GET /chat/repos`
No params. Returns `{"repos": [...]}` — **every** repo ever indexed by anyone (unfiltered by user), for the chat page's repo picker. `503` on Supabase failure.

---

## 5. `chat` — `docker/src/api/routers/chat.py` + `docker/src/chat/`

Header comment in the source: *"Unified chat endpoint: a single agent-first entry point replacing the old `/chat` (plain RAG) + `/agent/chat` (tool-using agent) split. Simple questions take a fast path with no tool calls automatically ... so there is no separate 'RAG mode' any more."*

### 5.1 Request shape — `ChatTurnRequest` (`docker/src/chat/models.py`)
Used identically by both `/chat` and `/chat/stream`:
```jsonc
{
  "question": "string",              // required
  "repo_url": "string",              // required
  "user_id": "string",               // required
  "chat_id": "string | null",        // omit/null to start a new session
  "current_page_context": "string | null"  // optional, prepended to the question as context
}
```
No `conversation_history` field — do not send one; it's ignored even if present. Missing `question`/`repo_url`/`user_id` → `400 "Missing question, repo_url or user_id"`. If the repo has no vector index yet (`repositories.vector_store_path` empty/missing) → `404 "No vector index for this repository. Generate documentation via /generate first."`.

### 5.2 `POST /chat` (non-streaming)
Runs the agent loop to completion, persists the assistant turn, returns:
```jsonc
// ChatTurnResponse
{
  "chat_id": "string",
  "repo_url": "string",
  "answer": "string",
  "sources": ["string", ...],
  "tool_trajectory": [
    {
      "tool": "string",
      "arguments": { /* the tool's actual call args */ },
      "status": "success" | "error",
      "summary": "string (truncated to 400 chars)",
      "duration_ms": "number | null"   // currently always null — not populated by the non-streaming path
    }
  ],
  "iterations": 0
}
```
No `mermaid`, `confidence`, `confidence_level`, `anchors_count`, `evidence_count`, or `caveats` fields — the new agent doesn't compute any of those (they were specific to the old, deleted `agent/graph.py`'s evaluate/synthesize phases).

### 5.3 `POST /chat/stream` (SSE)
`Content-Type: text/event-stream`, headers `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`. Each frame is `event: <name>\ndata: <json>\n\n` (`docker/src/chat/events.py::format_sse`). Event sequence:

| Event | When | `data` payload |
|---|---|---|
| `turn_start` | Immediately after session/path setup succeeds | `{"chat_id": string, "repo_url": string}` |
| `iteration_start` | Each time the `agent` (or `final_answer`) node starts | `{"iteration": number, "max_iterations": number}` |
| `tool_call_start` | A tool call begins | `{"tool": string, "arguments": object, "iteration": number}` |
| `tool_call_result` | A tool call resolves | `{"tool": string, "status": "success" \| "error", "summary": string (≤400 chars)}` |
| `answer_token` | Each streamed token of the model's final answer | `{"delta": string}` |
| `answer_done` | Final answer fully assembled | `{"answer": string, "sources": [string, ...]}` |
| `complete` | Turn fully persisted, stream about to close | `{"chat_id": string, "repo_url": string}` |
| `error` | Setup failed, or an exception during the turn | `{"detail": string}` |

This entirely replaces both prior vocabularies (old RAG: `retrieval_start`/`hyde_generated`/`retrieval_done`/`answer_delta`/`answer_done`; old Agent: `planning`/`subtask_parallel`/`tool_call`/`evaluation`/`synthesis`). Note `answer_token` (not `answer_delta`) is the new streaming-token event name.

The assistant turn is persisted (same `chat_messages` write, `metadata.mode: "agent"` always now) right before `complete` is emitted — same as the non-streaming path.

### 5.4 `GET /chat/history?user_id=...`
Returns `{"history": [...]}` — every `chat_history` row for that user, newest `updated_at` first, each with a `chat_id` field aliased from `id` for frontend convenience.

### 5.5 `GET /chat/messages/{chat_id}`
Returns `{"messages": [...]}` — all `chat_messages` rows for that session, oldest first.

### 5.6 `DELETE /chat/history/{chat_id}?user_id=...`
Deletes the session and its messages, only if `user_id` matches the owner. `400` if either param missing, `404` if not found/not owned, else `{"success": true}`.

---

## 6. `files` — `docker/src/api/routers/files.py`

### `POST /file/content`
Body: `{"repo_url": string, "file_path": string}` (both required). Reads a file from the locally-cloned repo checkout (path-traversal-safe via `resolve_path_under_root`). Returns `{"content": "string (utf-8, errors replaced)"}`. `404` if the repo checkout or file doesn't exist.

---

## 7. Chat agent tools (`docker/src/chat/tools/`)

Not directly HTTP-facing, but their names/args show up in `tool_trajectory` (§5.2) and `tool_call_start`/`tool_call_result` events (§5.3), and their `top_k`/thresholds are what frontend tool-call displays (`LiveStepFlow`, `LiveToolStep`) need to render meaningfully. Assembled per-turn by `build_tools_for_session()` — availability is conditional:

| Tool | Always available? | Args (`docker/src/chat/tools/schemas.py`) |
|---|---|---|
| `rag_search` | Yes (only needs the vector index) | `query: string`, `top_k: int = 20` |
| `code_graph` | Only if the repo's code graph file exists | `operation: "find_definition" \| "find_callers" \| "find_callees" \| "get_class_hierarchy" \| "get_file_symbols" \| "get_all_symbols" \| "find_imports" \| "get_module_dependencies"`, `symbol_name?: string`, `file_path?: string` |
| `file_read` | Only if the repo is checked out on disk | `file_path: string`, `start_line?: int`, `end_line?: int`, `max_lines: int = 100` |
| `repo_map` | Only if the repo is checked out on disk | `include_signatures: bool = true`, `max_depth: int = 3` |
| `grep_search` | Only if the repo is checked out on disk | `pattern: string`, `is_regex: bool = false`, `file_pattern?: string`, `max_results: int = 50`, `case_sensitive: bool = false`, `path_prefix?: string`, `context_lines: int = 2` |
| `web_search` | Opt-in via `repo_config.json`'s `web_search.enabled` (default true) | `query: string`, `search_type: "general" \| "code_docs" \| "version" \| "cve" = "general"`, `max_results: int = 5`, `domain_filter?: string` |

`lsp_resolve` (the old Agent's 7th tool) is gone entirely — not degraded, not replaced, just removed along with the `rope` dependency it needed.

The model decides which tools to call itself (native OpenAI/OpenRouter tool-calling via `bind_tools()`) — there's no separate planning/routing step choosing tools on its behalf.

---

## 8. Database schema (Supabase, `public` schema)

Verified live via `mcp__supabase__list_tables` (not from source comments) on 2026-09-19. **None of the 5 tables below have Row Level Security enabled** — every row is readable/writable by anyone holding the anon key. This is a pre-existing posture, not something introduced by recent changes, but worth knowing if the frontend starts trusting client-side auth to gate data access — right now, that gate does not exist at the database layer at all.

### `profiles`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid, PK | = `auth.users.id` |
| `email`, `phone`, `full_name`, `avatar_url` | text, nullable | |
| `updated_at` | timestamptz, default `now()` | |
| `language` | text, default `'zh'` | |
| `theme` | text, default `'dark'` | |

### `tasks`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid, PK, default `gen_random_uuid()` | |
| `user_id` | uuid, FK → `profiles.id` | |
| `task_id` | text | the id actually used in URLs/polling (`/task/{task_id}`) — distinct from `id` |
| `repo_url` | text | |
| `status` | text, default `'pending'` | `pending` \| `processing` \| `completed` \| `cached` \| `failed` |
| `created_at` | timestamptz, default `now()` | |
| `last_updated` | timestamptz, nullable | |
| `progress` | text, nullable | **stored as text**, coerced to float on read (`TaskRecord._coerce_progress`) |
| `current_step` | text, nullable | |
| `result` | json, nullable | see §3.2.1 |
| `error` | text, nullable | |
| `repo_name` | text, nullable | |

### `repositories`
| Column | Type | Notes |
|---|---|---|
| `repo_name` | text, **PK** | note: PK is `repo_name`, not `repo_url` |
| `repo_url` | text | |
| `r2_structure_url` | text, nullable | |
| `r2_content_urls` | text[], nullable | |
| `last_updated` | timestamptz, default `now()` | cache-freshness check (≤3 days) uses this |
| `vector_store_path` | text, nullable | recomputed locally via `path_resolver.py`, not trusted as an absolute path (§ CLAUDE.md) |
| `graph_path` | text, nullable | |
| `stargazers_count` | int4, nullable | |
| `github_short_description` | text, nullable | |

### `chat_history` (chat sessions)
| Column | Type | Notes |
|---|---|---|
| `id` | uuid, PK, default `gen_random_uuid()` | aliased as `chat_id` in API responses |
| `user_id` | uuid, FK → `profiles.id` | |
| `repo_url` | text | |
| `title` | text, nullable | |
| `preview_text` | text, nullable | |
| `created_at`, `updated_at` | timestamptz, default `now()` | `updated_at` bumped on every new message |

⚠️ **`session_summary` (text) and `summary_up_to_created_at` (timestamptz) do NOT exist on the live table yet** despite being read/written by `docker/src/chat/memory.py` and `SupabaseClient.get_chat_session`/`update_chat_session_summary`. See "Database migration not yet applied" in §0 for the exact SQL to run.

### `chat_messages`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid, PK, default `uuid_generate_v4()` | not sequential — this is why the summary cutoff (`chat_history.summary_up_to_created_at`) tracks `created_at`, not `id` |
| `chat_id` | uuid, FK → `chat_history.id` | |
| `role` | text, check `IN ('user','assistant')` | |
| `content` | text | |
| `metadata` | jsonb, default `{}` | for assistant messages: `{"sources": [...], "tool_trajectory": [...], "iterations": N, "mode": "agent"}` |
| `created_at` | timestamptz, default `now()` | |

### `repo_memory` (planned, not live)
Defined in `docker/sql/2026_chat_memory.sql` (`repo_url text PRIMARY KEY, facts jsonb, updated_at timestamptz`) but **not created in the live database** and **not read or written by any code yet** — a placeholder for a future durable-repo-facts feature. Don't build frontend UI against it.

---

## 9. Config surface relevant to chat behavior (`docker/config/repo_config.json`)

Read via named getters in `docker/src/config.py` — not hardcoded. Current values, under the `chat` key:

```json
{
  "max_tool_iterations": 6,
  "context_token_budget": 12000,
  "reserved_output_tokens": 2000,
  "history_summary_trigger_messages": 20,
  "hybrid_dense_weight": 0.6,
  "hybrid_sparse_weight": 0.4,
  "mmr_lambda": 0.5,
  "dense_k_multiplier": 4,
  "sparse_k_multiplier": 3,
  "max_method_k": 30,
  "max_total_candidates": 50,
  "category_top_k": {"code": 20, "text": 20},
  "hyde_enabled": true
}
```
`max_tool_iterations` directly bounds how many `iteration_start`/`tool_call_*` event rounds a `/chat/stream` turn can produce (§5.3) — useful if the frontend wants to size a live-progress UI. `web_search` config (provider/allowed domains/keys) lives under its own top-level `web_search` key, default provider `duckduckgo`.

Model-per-role (`ai_models.models`) relevant to chat: `chat_agent` (the main tool-calling model) and `chat_session_compressor` (background summarization model, once the §0 migration is applied).

---

## Files referenced throughout this doc

- `docker/scripts/api.py`
- `docker/src/api/routers/health.py`, `tasks.py`, `repos.py`, `files.py`, `chat.py`
- `docker/src/chat/models.py`, `service.py`, `graph.py`, `state.py`, `events.py`, `memory.py`, `prompts.py`, `tools/__init__.py`, `tools/schemas.py`
- `docker/src/core/chat_session.py`, `path_resolver.py`, `task_manager.py`, `wiki_pipeline.py`
- `docker/src/storage/supabase_client.py`, `models.py`
- `docker/src/config.py`, `docker/config/repo_config.json`
- `docker/sql/2026_chat_memory.sql`
- `frontend/src/lib/api.ts` (for the integration-gap comparison in §0)
