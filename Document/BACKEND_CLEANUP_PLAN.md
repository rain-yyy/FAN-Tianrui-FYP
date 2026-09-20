> **STATUS: COMPLETED (archived).** Every phase below (Phase 0 through Phase 6 — Tracks A, B, C, D, E) has landed; commit history from `47a8c5e` (Phase 1) through `d68562e` (code-review follow-up after Phase 6) tracks it item by item, and `git log --oneline` on `main` is the authoritative record of what changed and when. This document is kept as a **historical record of why** the current code shape looks the way it does (e.g. why `docker/src/paths.py` is a single source of truth, why `TaskStatus` is one `Enum`, why Supabase read methods are named the way they are) — it is no longer a to-do list. Do not treat any item below as outstanding without checking current source first. For current backend API/DB shape, see `Document/API_DOCUMENTATION.md`; note that a separate, unrelated, currently-uncommitted rewrite of the chat backend (`docker/src/chat/`, replacing `docker/src/agent/` + `docker/src/core/chat.py`) happened after this plan was completed and is documented there, not here.
>
> Original scope note below (§0) still describes what this plan covered at the time it was written; §1's "state of the codebase" narrative describes the *pre-cleanup* state, not the current one.

# Backend Cleanup Plan — Task Management, Supabase, Wiki Pipeline, RAG/Ingestion

*Scope: `docker/src` — task management, Supabase storage, wiki generation pipeline, and the RAG/ingestion machinery (code graph, community detection, Qdrant vector store, hybrid retrieval).*

*Out of scope (separate future task): `docker/src/agent/` (the Agent chat state machine and its tools) and the plain-RAG chat orchestration logic in `docker/src/core/chat.py`.*

*Produced from a full audit of the three in-scope areas, each independently explored with file:line citations, then synthesized into this single roadmap. Four items where cleanup would change observed behavior (not just code shape) were flagged and decided with the project owner before being finalized here — see §4.*

---

## 1. State of the codebase

The core pipeline — task creation → wiki generation → RAG indexing → Supabase persistence — works, and the recent Qdrant migration (commit `7bc6fda`) was completed cleanly: there is no leftover dead FAISS code anywhere in the audited scope, `kb_loader.py` was correctly deleted, and `faiss-cpu` is gone from `requirements.txt`. That said, the surrounding code shows classic "grown, not designed" symptoms:

- **Three independently-computed `PROJECT_ROOT` constants** (`docker/scripts/api.py`, `docker/src/config.py`, `docker/src/core/wiki_pipeline.py`) that will silently diverge if any file is moved.
- **Two definitions of `TaskStatus`** (a canonical `Enum` in `task_manager.py`, a stale plain-string duplicate in `wiki_pipeline.py` whose comment claims consistency with a file that no longer defines it).
- **Three different "repo name" helpers** that disagree with each other (owner/repo vs. last-path-segment vs. a third inline reimplementation).
- **Two Supabase write paths for the same repository data**, one of which appears to be silently broken.
- **At least six self-flagged `# TODO` comments** where the original author already suspected duplication or a bug but never resolved it (e.g. `supabase_client.py:220,262,280,314,339`).
- **No automated test suite anywhere in `docker/`** — only two ad hoc manual scripts (`test_faiss.py`, `test_rag_tool.py`) invoked directly, not a real suite.

There is a meaningful amount of code that can be deleted outright with effectively zero behavioral risk — unused Supabase methods, dead constants, a ~240-line debug `__main__` block sitting inside a production module, and a half-wired feature whose other half was never connected. Doing that first would materially shrink the codebase before any riskier work begins.

---

## 2. Action items by track

Each item lists what to do, where, why (tied to the specific audit finding), and a risk/effort tier. The tiers reflect how likely a change is to be safe given there is **no automated regression safety net** in this repo — see §5.

### Track A — Dead code removal (do first; no behavior change by construction)

| # | Item | File(s)/Lines | Why |
|---|---|---|---|
| A1 | Delete `get_profile`, `upsert_profile_preferences` | `supabase_client.py:555-589` | Zero call sites anywhere; no `/profile` route exists at all. |
| A2 | Delete `get_repositories_for_urls` | `supabase_client.py:221-260` | Zero call sites; carries its own `# TODO: what is the purpose of this function` (line 220). |
| A3 | Delete `DEFAULT_OUTPUT_PATH`, `DEFAULT_WIKI_SECTION_JSON_OUTPUT` | `wiki_pipeline.py:37-38` | Comment admits "kept as a single-task fallback"; `execute_generation_task` always uses `_task_output_dir()` instead. |
| A4 | Delete the embedded `if __name__ == "__main__"` smoke-test harness | `code_graph.py:1272-1509` (~240 lines, ~16% of the file) | Doesn't belong in a production module; explains the stray ~25k-line `docker/code_graph.json` debug artifact that got committed alongside the Qdrant migration. Move to a standalone script if the smoke test is still wanted. |
| A5 | Delete `__main__` blocks hardcoded to the original developer's absolute machine path | `struct_gen.py:411-420`, `file_processor.py` (bottom block) | Dead/unusable for anyone else; confuses readers. |
| A6 | Delete `get_node_community()` | `community_engine.py:408-412` | Zero call sites anywhere. |
| A7 | Delete unused `output_dir` constructor param | `content_gen.py:53` | Comment states outright: "kept for backward-compat callers, no longer used." Confirmed no caller passes it. |
| A8 | Delete unused `config_path` param on `get_files_to_process`, unused `config` dict param on `split_code_and_text_files`; update both call sites | `file_processor.py:49-60,96-97`; callers in `wiki_pipeline.py:229,236` | Neither parameter is read in its function body (both use module-level `CODE_SUFFIXES`/`TEXT_SUFFIXES` constants instead); callers currently load and pass a config that has zero effect. |
| A9 | Delete `repository.max_size_mb` from `repo_config.json` (or wire it up — see Track E note) | `config/repo_config.json` | Zero references anywhere in `docker/src`; implies a size-enforcement feature that doesn't exist. |
| A10 | Remove the discarded `_ = load_config(str(config_path))` line | `wiki_pipeline.py:153` | Loads config purely "to validate config file validity" but discards it; provides no behavior beyond what `run_rag_indexing`'s own `load_config` call already does downstream. |
| A11 | Fix the unused-`Sequence` type-annotation bug | `content_gen.py:350` | `Sequence` is never imported; only avoids a `NameError` because `from __future__ import annotations` makes it a lazy string. Add the import or drop the annotation. |

**Verification method for this track:** `grep -rn "<symbol name>"` across `docker/` before deleting each symbol; confirm zero hits outside its own definition. With no test suite, this grep pass *is* the safety net — do it symbol-by-symbol, not as one giant sweep.

### Track B — Duplicate/conflicting logic to consolidate

| # | Item | File(s)/Lines | Why | Risk/Effort |
|---|---|---|---|---|
| B1 | Unify `TaskStatus` onto the canonical `Enum` | `task_manager.py:13-18` (keep) vs. `wiki_pipeline.py:24-29` (delete duplicate, import the Enum instead) | Two independently-maintained sources of truth for the same status strings; the `str` `Enum` is drop-in compatible with existing string comparisons/serialization. | Low |
| B2 | Unify the three "repo name" concepts into distinctly-named functions | `supabase_client.py:_get_repo_name` (44-45, "owner/repo") vs. `setup_repository.py:get_repo_name`/`get_repo_disk_directory_name` (31-38, last segment) vs. `path_resolver.py:43` (inline reimplementation of the single-segment version, despite already importing the real helper on line 10) | Three inconsistent notions of "repo name" causing confusion between DB column values and on-disk directory names. Rename to distinct names (e.g. `repo_owner_slug()` vs. `repo_disk_dirname()`) and fix `path_resolver.py:43` to import instead of reimplement. | Medium — touches DB-facing and disk-path code; do together with D1/D2 (§3, Phase 3). |
| B3 | Merge or clearly differentiate `get_all_indexed_repos` (204) and `get_all_repositories_metadata` (262) | `supabase_client.py:204,262` | Both read the same `repositories` table with no shared implementation; the author's own TODO on line 262 asks "what is the purpose of this function." | Medium — diff actual query/column differences before merging; check `repos.py` call sites don't depend on a subtle difference. |
| B4 | Resolve the `get_repo_wiki_artifacts` / `get_user_dashboard_repositories` / `get_all_indexed_repos` overlap | `supabase_client.py:314,339,369`; `repos.py:68` (same open question restated independently) | Same confusion raised at two separate layers (storage + router) with no resolution at either. | Medium |
| B5 | Consolidate ad hoc JSON-string coercion outside `models.py`'s `TaskRecord` | `models.py` (the reusable pattern) vs. manual duplicates in `supabase_client.py:327-328` and `wiki_pipeline.py:487-488` | Same `isinstance(x, str)` → parse normalization implemented ad hoc in multiple places instead of reusing the existing model-based approach. | Low |
| B6 | Extract the shared Supabase orchestration sequence duplicated across chat routers | `chat.py` and `agent.py` (Supabase-facing portions only: create session → save user message → fetch vector path → run retrieval → save assistant message) | Near-verbatim copy-paste across two router files. | Medium — limit strictly to extracting the Supabase orchestration wrapper; do not touch chat/agent internals (out of scope). |
| B7 | Dedupe `wiki_pipeline.py`'s inline cache-hit normalization against `get_repo_wiki_artifacts` | `wiki_pipeline.py:480-502` vs. `supabase_client.py:315-337` | Same `r2_content_urls` str→list normalization and truthy check implemented independently in two files that will drift. | Low-Medium |
| B8 | Collapse the nested JSON-parsing fallback chains for LLM output | `struct_gen.py`: `parse_wiki_structure_json` "Strategy 0" (276-306) and `_parse_llm_json` (218-255), plus the hand-rolled `_extract_balanced_braces` (187-215) | Up to 5-6 independent parsing attempts for a single LLM response — papering over unreliable output rather than fixing it at the source. | Medium — the real fix is requesting `response_format={"type":"json_object"}` (or provider equivalent) at the LLM call site, then collapsing both fallback chains into one helper. Needs a live LLM call to verify; higher behavioral risk than pure refactor. |
| B9 | Dedupe `r2_client.py`'s near-identical `upload_file`/`upload_json_data` | `r2_client.py:114-173,175-230` | Same retry/backoff/exception structure (~60 lines each), differ only in byte source. Extract a shared "upload bytes with retry" helper. | Low |
| B10 | Reuse `_get_r2_path` instead of recomputing the key format inline | `r2_client.py:96-112` vs. `319-325` | Same R2 key format (`{repo_name}/{date}[_{task_id}]/{filename}`) computed twice in the same file. | Trivial-Low |
| B11 | Unify the three parallel dense+sparse fusion implementations | `retrieval.py`: `CommunityFirstRetriever.retrieve()` (409-444), `hybrid_retrieve()` (446-526), `qdrant_search_category()` (529-589) | Same normalize+weight fusion pattern implemented three times independently. | Medium — in-scope (RAG-internal) but touches retrieval quality; manually compare a few sample queries before/after since there are no tests. |
| B12 | Reuse the existing `_name_to_nodes` index instead of duplicated linear scans | `code_graph.py:_py_semantic` (1084-1108), `_js_semantic` (1121-1158) | Both independently run a full O(V) graph scan to resolve inheritance/implements targets by name, duplicated between the two methods, without using the pre-built index the call-edge resolver already uses for the same purpose. | Medium |

### Track C — Correctness bugs (decisions already made — see §4 for rationale)

| # | Item | File(s)/Lines | Decision |
|---|---|---|---|
| C1 | `valid_file_list` anti-hallucination constraint is always empty | `struct_gen.py:88,116,165` (param always `None`/`""`; `filtered_file_paths` is computed at line 116 but only fed to the code-graph builder, never the prompt); `prompts.py:97-103` (an entire "CRITICAL CONSTRAINT" section referencing an always-empty `<VALID_FILE_LIST>`) | **Wire it up.** Pass `filtered_file_paths` into the `chain.invoke(...)` call's `valid_file_list` field instead of leaving it empty. This changes LLM output on every wiki generation — spot-check against a couple of real repos manually after the change. |
| C2 | Code graph is built and persisted but never fed to the content generator | `content_gen.py:75,77` (`self.graph`, `set_graph()` — zero call sites anywhere); `_collect_file_context`'s "[逻辑依赖]" dependency-enrichment branch (208-214) is unreachable as a result; `from src.ingestion.code_graph import CodeGraphBuilder` (line 38) is an unused import | **Delete the dead half.** Remove `set_graph()`, `self.graph`, the unreachable branch, and the unused import. This is unfinished work being retired, not a regression — re-finishing it (loading the persisted graph back and wiring it into content generation) is a feature project, not a cleanup item. |
| C3 | `update_repository_vector_path` upsert payload doesn't set the key its own docstring claims to match on | `supabase_client.py:47-68` (docstring: "the `repo_url` is the primary key, so upsert will use it to match"; actual payload at 58-62 sets only `repo_name`, `vector_store_path`, `last_updated` — never `repo_url`); wrapper `update_repo_vector_path` (592-597) passes a full URL positionally into a param literally named `repo_name`; the method swallows all exceptions and returns `False`, making `wiki_pipeline.py:294-297`'s wrapping `try/except` around it dead/unreachable code | **Delete the legacy path, route through `upsert_repo_wiki_data`.** Remove `update_repository_vector_path` and its module-level wrapper; point `run_rag_indexing`'s one remaining call site (`wiki_pipeline.py:295`) at the already-correct, already-actively-used `upsert_repo_wiki_data` (`supabase_client.py:506-551`), which properly keys on `repo_url`. |
| C4 | Wiki-generation cache never verifies underlying artifacts, and a fully-failed RAG retry never updates the repository row | `wiki_cache_policy.py` (`wiki_generation_cache_is_stale` compares only `repositories.last_updated` against a 3-day TTL — never checks R2 object existence or vector-index health); `wiki_pipeline.py:480-502` (serves a "CACHED" hit straight off the DB row with no R2 HEAD/GET check); the RAG-retry-exhaustion failure branch (`wiki_pipeline.py:~461`) updates only `tasks.result`, never calling `upsert_repo_wiki_data`, so `repositories.vector_store_path` can remain null/stale while `r2_structure_url`/`r2_content_urls` (wiki succeeded even though RAG failed) are present | **Fix the failure-branch write-back only.** Make the RAG-retry-exhaustion failure branch call `upsert_repo_wiki_data` (or equivalent) to record the partial-failure state, so a wiki-succeeded-but-RAG-failed repo is not served as a full cache hit on the next `/generate` within the 3-day window. **Explicitly skip adding an R2 existence/HEAD check for now** — it's a genuine latency/API-cost vs. correctness tradeoff, deferred until stale-URL issues are actually observed in practice, not pre-emptively built. |
| C5 | Background RAG retry escapes the task-cancellation registry | `wiki_pipeline.py:358-462` `_background_retry_rag_indexing`, spawned via bare `asyncio.create_task` (641-643) outside `task_manager._running_tasks` | Flag alongside C4 (same phase, same file): `/task/{id}/cancel` cannot stop a scheduled RAG retry once the parent task already reports "completed." Register the retry task in `_running_tasks` (or an equivalent tracked registry) so cancellation actually reaches it. |

### Track D — Coupling to break

| # | Item | File(s)/Lines | Why | Risk/Effort |
|---|---|---|---|---|
| D1 | Consolidate the three `PROJECT_ROOT` computations into one shared constant | `scripts/api.py:5`, `src/config.py:11`, `src/core/wiki_pipeline.py:34` (each computes via a different relative `Path(__file__).parent...` chain) | Will silently diverge if any file moves; `setup_repository.py:8` has its own TODO flagging exactly this problem. | Medium — touches 3+ import-order-sensitive files; do together with D2. |
| D2 | Fix `path_resolver.py`'s backwards dependency on `wiki_pipeline.py` | `path_resolver.py` imports `VECTOR_STORE_ROOT`/`REPO_STORE_ROOT` **from** `wiki_pipeline.py` | A module meant to be a shared path-handling utility depends on the big orchestrator it's supposed to serve — backwards. Do together with D1 and B2, since all three touch the same conceptual "path handling module" problem `setup_repository.py` already calls for. | Medium |
| D3 | Centralize scattered Supabase writes in `wiki_pipeline.py` | `SupabaseClient()` instantiated inline in 4 different functions; `run_rag_indexing` writes to Supabase itself mid-function instead of returning data for the orchestrator to persist | Spreads Supabase writes across multiple functions, making C3/C4 harder to reason about and verify. | Medium-High — do alongside the C3/C4 fixes, not standalone. |
| D4 | Share/inject a single `SupabaseClient` instead of instantiating inline per handler | `tasks.py:29,60,86,97,147`; `wiki_pipeline.py` (4 sites) | Repeated per-request/per-function construction instead of a shared or FastAPI-injected client. | Low-Medium |
| D5 | Extract `cancel_task_api`'s 3-branch state machine into a service method | `tasks.py:91-136` | In-memory-running / DB-PROCESSING-after-restart / DB-PENDING branching lives directly in the route handler instead of a testable service method. | Low-Medium |
| D6 | Fix `delete_task_api`'s duplicated-effort 404-vs-500 disambiguation | `tasks.py:139-159` | `delete_task` already determines the failure reason internally but returns a bare `False`; the router re-fetches via `get_task` just to disambiguate. Have `delete_task` return/raise a typed result instead. | Low |
| D7 | Return a named result type instead of a raw 4-tuple from `upload_wiki_to_r2` | `r2_client.py` (return value), consumed positionally in `wiki_pipeline.py:553` | Adding a 5th artifact type would require lockstep changes in two files with no type safety. Use a small dataclass/TypedDict. | Low |
| D8 | Standardize `SupabaseClient`'s two incompatible error-handling conventions | `supabase_client.py`: ~15 methods swallow exceptions + bare `print()` + return `False`/`None`/`[]`, while `get_task` and `get_all_indexed_repos` raise `SupabaseStorageError` | Forces every caller to juggle both falsy-checks and try/except. Pick one convention (raising is more consistent with `TaskRecord`/`SupabaseStorageError` already existing) and migrate. | Medium-High — do **last**, after Track B's method-merging has already reduced the number of methods to update; requires a full call-site audit. |
| D9 | Split `execute_generation_task` into named stages | `wiki_pipeline.py:465-661` (~200 lines: cache lookup, path setup, 4 pipeline stages, 3 exception types, result assembly, write-back, cleanup, all in one function) | Cannot currently unit-test any single stage in isolation. | Medium-High — behavior-preserving extract-method only; verify with a manual run before/after. |
| D10 | Split `cleanup_local_files`'s three fallback deletion strategies into named, independently-reasoned branches | `wiki_pipeline.py:305-355` | Each guarded by a broad `except Exception`; currently hard to know which branch actually runs in a given deployment. | Low-Medium |
| D11 | Split `content_gen.py`'s `generate()` into named steps | `content_gen.py:80-165` | TOC flattening, filename-collision resolution, concurrency-limit computation, thread-pool submission, a nested worker closure with its own locking/progress math, and result aggregation all live in one ~85-line method. | Medium |
| D12 | Split `community_engine.py`'s `_run_leiden_file_collapsed` into named steps | `community_engine.py:125-240` | Edge-weight computation, hub dampening, directory-chain augmentation, igraph construction, and community expansion all in one ~115-line method. | Medium |
| D13 | Fix the circular import between ingestion and core layers | `vector_store.py:107-110` (`_doc_key()` does a function-local `from src.core.retrieval import compute_doc_key`) vs. `retrieval.py:552-553` (`qdrant_search_category` does the reverse: function-local `from src.ingestion.vector_store import payload_to_document, query_dense, query_sparse`) | A genuine circular dependency between "ingestion" and "core," currently hidden by pushing both imports inside function bodies instead of fixing the layering. | Medium — extract `compute_doc_key`/`payload_to_document` into a shared lower-level module (e.g. a new `src/core/shared_types.py`) both import at module scope. |

### Track E — Config to centralize

| # | Item | File(s)/Lines | Why |
|---|---|---|---|
| E1 | Move `OPENROUTER_EMBEDDING_MODEL` into `repo_config.json`'s `ai_models.models` | `embedding_utils.py:31` | The one model choice not centralized like `hyde_generation`/`rag_answer`/`wiki_structure`/`community_summary`, all of which already go through `get_model_name()`/`get_llm()`. Likely an oversight from the Qdrant migration. |
| E2 | Move batch-size/sleep tuning constants into `repo_config.json` | `embedding_utils.py` (`INNER_BATCH_SIZE`, `INNER_BATCH_SLEEP_SEC`) vs. `vector_store.py` (`EMBEDDING_BATCH_SIZE`, `INTER_BATCH_SLEEP_SEC`) | Two independent hardcoded layers of the same kind of constant, neither configurable. |
| E3 | Move chunking constants into `repo_config.json` | `docu_splitter.py` (`MAX_CHUNK_SIZE=2000`, `OVERLAP_SIZE=150`, `MIN_CODE_CHUNK=150`, `MAX_CODE_CHUNK=2000`) | Same pattern — hardcoded instead of config-driven like `community_detection` already is. |
| E4 | Move hardcoded RAG-retry delays into config | `wiki_pipeline.py:358-462` (`[30, 120, 300]` seconds, hardcoded) | Same centralization gap. |
| E5 | Gate debug-output writers behind a config/env flag | `struct_gen.py:170-180` (unconditional timestamped raw-response dump to `./wiki_structure_raw/`, no retention/cleanup, not covered by `cleanup_local_files`); `docu_splitter.py`'s `save_chunks_debug()` (called unconditionally from `wiki_pipeline.py`) | Unbounded disk growth in a long-lived server process — an operational bug, not just style. Fix regardless of which other phase is in progress; it's a one-line-per-site change. |
| E6 | Decide the fate of `repository.max_size_mb` | `repo_config.json` | Either wire it into real repo-size enforcement in `file_processor.py`/`setup_repository.py`, or delete it (A9) — currently implies a safety feature that was never built. |

---

## 3. Phasing recommendation

1. **Phase 0 — Baseline.** Before touching anything, write down and run a short manual smoke checklist once, to know current (possibly already-buggy) behavior: create task → generate wiki (cache miss) → generate wiki (cache hit) → cancel task → RAG query returns results → delete task.
2. **Phase 1 — Track A (dead code deletion).** Zero behavior change by construction. Grep-verify each symbol individually before deleting. Highest value for the "codebase is too big" concern at the lowest risk — do this as one focused pass.
3. **Phase 2 — Low-risk, unambiguous consolidation.** B1 (TaskStatus), B5, B9, B10, D6, D7 — the "correct" behavior is unambiguous here, so these shrink the codebase further with minimal risk before harder work begins.
4. **Phase 3 — Path/name unification, done together.** D1 + D2 + B2. All three touch the same conceptual problem (`PROJECT_ROOT` triplication, `path_resolver.py`'s backwards dependency, the three colliding repo-name helpers) and overlapping files (`setup_repository.py`, `config.py`, `path_resolver.py`, `wiki_pipeline.py`, `supabase_client.py`). Doing them in one coordinated pass avoids fixing one and re-breaking it via another. Re-run the Phase 0 smoke checklist afterward — this phase touches import order and path construction broadly enough that a subtle break might not otherwise surface.
5. **Phase 4 — Track C correctness fixes, each as its own separate, reviewable change.** C1-C5. These change behavior; keep them out of Phase 1-3 commits entirely, since they're the highest-value and highest-risk items and the ones most likely to need an isolated rollback.
6. **Phase 5 — Remaining consolidation/decomposition.** B3, B4, B6, B7, B8, B11, B12, D3, D4, D5, D8-D13. Bigger refactors, done one at a time as their own reviewable changes, after Phases 1-4 have already shrunk and clarified the codebase. D8 (Supabase error-handling standardization) specifically goes last, after B3/B4's method-merging reduces the surface area it needs to touch.
7. **Phase 6 — Track E (config centralization).** Independent of the above; interleave wherever convenient. Do E5 (gating debug writers) opportunistically early since it's low-ambiguity and fixes a real operational issue regardless of what else is in flight.

---

## 4. Decisions on the four behavior-changing items

These were not pre-decided by the audit — they were reviewed and resolved with the project owner before being written into Track C above, because in each case "clean up the code" and "change what the system does" pull in different directions:

- **C1 (`valid_file_list`)** → wire it up rather than delete, since the computation (`filtered_file_paths`) already exists and wiring it through is a small, contained change — but it changes LLM output on every wiki generation, so it needs a manual before/after check against real repos.
- **C2 (`set_graph`/code-graph-in-content-gen)** → delete the dead half rather than finish it, since this was genuinely unfinished work (the graph is built and uploaded but never loaded back), and finishing it is a feature project with its own scope, not a cleanup item.
- **C3 (`update_repository_vector_path`)** → delete the legacy path and route through `upsert_repo_wiki_data` rather than patch the bug in place, since the newer method already does this correctly and is actively used elsewhere — keeping both would preserve exactly the kind of duplication this cleanup is meant to remove.
- **C4 (cache staleness)** → fix only the failure-branch write-back gap (clearly correct, low risk) and explicitly defer the R2-existence-check tradeoff (real latency/cost vs. correctness decision) rather than assuming either direction.

---

## 5. Regression-risk mitigation (no test suite exists)

- Confirmed independently across all three audits: there are no real automated tests anywhere in `docker/` — only two ad hoc manual scripts referenced in `CLAUDE.md` (`test_faiss.py`, `test_rag_tool.py`), not a suite.
- Do Track A (dead-code-only) deletions first and grep-verify each one individually — the only category of change that is safe by construction here.
- Before touching any cache/write-path/status logic (Phases 3-5), run the Phase 0 manual smoke checklist before *and* after each change, not just once at the end.
- Keep each phase/item as its own separate, individually revertible commit rather than one large refactor — with no CI safety net, `git revert`-ability on a single item is the main practical safety mechanism available.
- Treat the four Track C decisions (§4) as already resolved — do not relitigate fix-vs-delete mid-implementation; if new information surfaces that changes the calculus, re-raise it explicitly rather than silently picking a different path.
- Consider a small number of pure-logic smoke checks around the Track C changes specifically (e.g. hand-constructed `repo_info` dicts fed into `wiki_generation_cache_is_stale` before touching C4) — this logic has no external dependencies and is cheap to pin down, unlike the rest of the pipeline.

---

## 6. Estimated code volume reduction

| Source | Approx. lines removable |
|---|---|
| `code_graph.py` `__main__` smoke-test block | ~240 |
| `supabase_client.py`: `get_profile`, `upsert_profile_preferences`, `get_repositories_for_urls` | ~85 |
| `supabase_client.py`: `update_repository_vector_path` + wrapper (per C3 decision) | ~30 |
| `wiki_pipeline.py`: dead `TaskStatus` duplicate, dead `DEFAULT_*` constants | ~15 |
| `struct_gen.py` / `file_processor.py`: dead `__main__` blocks | ~30 |
| `content_gen.py`: unused `output_dir` param, dead `set_graph`/`self.graph`/unused import (per C2 decision) | ~15 |
| `community_engine.py`: `get_node_community` | ~5 |
| Misc dead params, discarded assignments, dead config keys | ~10 |
| **Total from pure dead-code removal (Track A + the two Track C deletions)** | **~430 lines, roughly 10-12% of the ~3,900 lines across the 10 core files audited** |

This is a conservative floor — it counts only code deletable with zero behavior change. Combining Phase 1 (dead code) with Phase 2/5 (duplicate consolidation — e.g. merging overlapping Supabase read methods, unifying the three retrieval-fusion implementations, deduplicating R2 upload helpers) plausibly brings total volume down **15-20%** without altering any product behavior, before any of the Track C behavior changes are even applied.

---

## Files referenced throughout this plan

- `docker/src/storage/supabase_client.py`
- `docker/src/core/wiki_pipeline.py`
- `docker/src/wiki/struct_gen.py`
- `docker/src/wiki/content_gen.py`
- `docker/src/api/routers/tasks.py`, `repos.py`, `files.py`
- `docker/src/core/task_manager.py`
- `docker/src/core/path_resolver.py`
- `docker/scripts/setup_repository.py`, `docker/scripts/api.py`
- `docker/src/ingestion/vector_store.py`, `docker/src/core/retrieval.py`
- `docker/src/ingestion/code_graph.py`, `community_engine.py`, `docu_splitter.py`, `embedding_utils.py`, `file_processor.py`
- `docker/src/storage/r2_client.py`
- `docker/src/storage/models.py`
- `docker/src/utils/wiki_cache_policy.py`, `path_safety.py`, `json_utils.py`
- `docker/config/repo_config.json`
