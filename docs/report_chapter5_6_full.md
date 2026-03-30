# Chapter 5: Testing and Evaluation

## 5.1 Testing Environment and Dataset

### 5.1.1 Deployment Infrastructure

The system operates across two deployment tiers:

**Backend (Fly.io)**
- **Platform**: Fly.io managed containers (application: `fan-tianrui-fyp`)
- **Region**: Singapore (`sin`)
- **Machine**: Fly.io shared-CPU instances with persistent volume mounts (`rag_data → /data`)
- **Runtime**: Python 3.10+, containerised via custom `Dockerfile`
- **Internal Port**: 8000 (FastAPI + Uvicorn)
- **Persistent Storage**: Fly Volume mounted at `/data`; subdirectories `vector_stores/` (FAISS + BM25 indices) and `repos/` (cloned repository snapshots)
- **HTTPS**: Enforced by Fly.io proxy; `auto_stop_machines = "off"` ensures at least one instance stays warm for long-running generation tasks

**Frontend (Vercel)**
- **Framework**: Next.js (React Router SPA via `RouterApp.tsx`; SSR disabled)
- **API Base**: `NEXT_PUBLIC_API_URL` (backend Fly.io endpoint)
- **Authentication**: Supabase Auth; profile-based `language`/`theme` propagation
- **Role**: Serves the interactive UI only; no RAG computation occurs at the edge

**External Services**
- **Supabase**: PostgreSQL database for tasks, repositories, chat history, user profiles
- **Cloudflare R2**: Object storage for generated Wiki JSON (structure + section pages)
- **OpenRouter**: Unified LLM gateway supporting Qwen, DeepSeek, Gemini models

### 5.1.2 Test Repositories

Three repositories of varying scale and language mix were selected for evaluation:

| Repository | Primary Language | Approx. Files | Description |
|-----------|-----------------|---------------|-------------|
| reactive-resume | TypeScript / React | ~200+ | Open-source resume builder (frontend-heavy) |
| MinerU | Python | ~300+ | Document parsing and conversion toolkit |
| autoresearch | Python | ~20 | LLM-based research automation (small-scale) |

These repositories are stored under `data/repos/` and referenced in `docker/config/repo_config.json` for file filtering rules (exclusion of lock files, build artifacts, configuration files, etc.).

### 5.1.3 Model Configuration

The system employs multi-model dispatch through OpenRouter:

| Task | Model | Purpose |
|------|-------|---------|
| Wiki Structure | `google/gemini-2.5-flash` | Generates Table of Contents from code graph |
| Wiki Content | `qwen/qwen3.5-flash-02-23` | Section-level Markdown + Mermaid generation |
| HyDE Generation | `google/gemini-2.5-flash` | Hypothetical document for retrieval |
| RAG Answer | `qwen/qwen3-235b-a22b-2507` | Final answer synthesis |
| Embeddings | `baai/bge-m3` | Dense vector embeddings |

---

## 5.2 Functional Verification

All automated tests reside in `docker/tests/` and are executed with PyTest:

```bash
cd docker && PYTHONPATH=. pytest tests/ -v
```

### 5.2.1 Unit Testing

#### A. Filename Normalisation and Deduplication (`test_content_gen.py`)

The `WikiContentGenerator` class in `docker/src/wiki/content_gen.py` must produce unique, filesystem-safe filenames for each Wiki section. The test suite covers:

- **`TestSafeFilename`** (UT-01 to UT-05): Validates that `_safe_filename()` correctly converts titles to lowercase-hyphenated slugs, strips illegal characters, and falls back to `"section"` for empty or entirely illegal input. Version-style strings like `"v1.0-beta"` preserve dots and hyphens as expected.

- **`TestNormalizeForCollision`** (UT-06, UT-07): Ensures the collision-detection normaliser folds both case and symbol variants (`API_Reference` ≡ `api-reference`) into the same canonical form, so the deduplication logic in `_build_filename_map` can detect near-duplicates.

- **`TestBuildFilenameMap`** (UT-08 to UT-10): Given lists of section IDs that hash to the same normalised form (e.g., `"API Reference"`, `"api-reference"`, `"API_reference"`), the mapping appends numeric suffixes (`api-reference`, `api-reference-2`, `api-reference-3`) to guarantee uniqueness. Edge cases — empty strings, pure symbols — all fall back to `"section"` with incremental suffixes.

#### B. Concurrent Generation and Isolation (`test_content_gen.py`)

- **`TestConcurrentGeneration`** (UT-11 to UT-13):
  - 10 sections at `max_concurrency=3` all produce unique JSON output files.
  - A deliberately flaky mock client (raising `RuntimeError` on one invocation) does not block other sections; at least 4 of 5 succeed.
  - The progress callback fires once per completed section, with values between 50.0 and 85.0 (matching the pipeline's design).

- **`TestCleanupIsolation`** (UT-14): Simulates two concurrent task directories; cleaning one (`task-a`) must not delete the other (`task-b`).

#### C. Tree-sitter Parsing Accuracy (`test_ts_parser.py`)

The `TreeSitterParser` class in `docker/src/ingestion/ts_parser.py` is the foundation of the code analysis pipeline. Tests use `pytest.importorskip` plus a `_SKIP_NO_LANGS` marker so that environments without tree-sitter language packs see `SKIPPED` rather than `FAILED`:

- **Python parsing** (UT-15 to UT-16): A snippet containing `def hello()` and `class Greeter` is parsed; assertions verify that the correct `node_type` (`function_definition` / `class_definition`) and `name` attributes are extracted.

- **JavaScript parsing** (UT-17): A `function add()` and `class Calculator` are identified via `function_declaration` and `class_declaration` node types.

- **TypeScript parsing** (UT-18): An `interface User` is detected as `interface_declaration`; `function getUser` and `type Status` are also extracted.

- **TSX parsing** (UT-19): A React component function `App()` is identified through the `tsx` language grammar.

- **Edge cases** (UT-20 to UT-22):
  - An unsupported extension (`.xyz`) returns an empty list.
  - Minimal code with no function/class definitions (e.g., `x = 1`) triggers the **file-level fallback** — the entire content is returned as a single `CodeChunk` with `node_type="file"` (lines 98–105 of `ts_parser.py`).
  - Syntactically invalid code does not raise exceptions.
  - `.jsx` files correctly map to the JavaScript parser.

### 5.2.2 Integration Testing (`test_wiki_pipeline_integration.py`)

The complete pipeline — `execute_generation_task` in `docker/src/core/wiki_pipeline.py` — orchestrates cloning, structure generation, content generation, R2 upload, and RAG indexing. Since the real pipeline requires Git, LLM API keys, Supabase, and R2, integration tests use `unittest.mock.patch` to replace every external dependency with lightweight stubs.

**Core Tests:**

- **IT-01: Structure Generation Writes JSON** — `run_structure_generation` is called with a mocked `setup_repository` (returning a temporary directory) and a mocked `generate_wiki_structure` (returning a known structure). The test asserts that the output JSON file is created and contains the expected `title` and `toc` fields.

- **IT-02: Local Path Bypass** — When `repo_url_or_path` is a filesystem path (not a URL), `setup_repository` is never invoked, verifying that the local-path fast path works correctly.

- **IT-03: Content Generation Produces Files** — `run_wiki_content_generation` with a mock AI client that returns valid JSON produces one output file per section (4 sections → 4 `.json` files).

- **IT-04: Progress Callback** — With a patched `_update_progress`, the content generation stage triggers at least 2 progress updates (initialisation + completion).

- **IT-05: Full Pipeline Mock** — `execute_generation_task` is invoked with all sub-steps mocked; the test verifies that each step was called exactly once and that `update_task_status` was invoked to mark completion.

- **IT-06: Task Directory Isolation** — Two independent task IDs produce separate working directories under `TASK_WORK_ROOT`; modifying one does not affect the other.

**Cleanup Tests:**

- **IT-07: Task Directory Cleanup** — `cleanup_local_files` removes the entire task directory when `output_path` is inside `TASK_WORK_ROOT`.
- **IT-08: Cross-Task Isolation** — Cleaning `task-a` preserves `task-b` and its contents.

---

## 5.3 Quantitative Performance Analysis

### 5.3.1 Wiki Generation Efficiency

The end-to-end pipeline (`execute_generation_task`) progresses through six stages, each tracked via Supabase progress updates:

| Stage | Progress Range | Description |
|-------|---------------|-------------|
| 1. Clone / File Tree | 5%–20% | `setup_repository` + `generate_file_tree` |
| 2. GraphRAG Community Discovery | 20%–30% | `CodeGraphBuilder` → `CommunityEngine.run_leiden()` |
| 3. Structure Generation (LLM) | 30%–40% | `generate_wiki_structure` with Gemini |
| 4. Content Generation (Concurrent) | 45%–85% | `WikiContentGenerator.generate` via `ThreadPoolExecutor(max_workers=3)` |
| 5. R2 Upload | 86%–88% | `upload_wiki_to_r2` |
| 6. RAG Index Build | 88%–91% | `run_rag_indexing` (FAISS + BM25) |

From IR II experimental runs, observed total times range **14–25 minutes** per repository, producing **30–50 Wiki pages**. Stage 4 (concurrent LLM content generation) dominates wall-clock time at approximately 60–70% of the total.

A benchmarking script (`test/chapter5/03_benchmark_rag_latency.py`) is provided for reproducible measurement. The timing template (`test/chapter5/03_wiki_pipeline_timings.md`) records per-stage durations across test repositories.

### 5.3.2 RAG Response Latency

The RAG query path (`answer_question` in `docker/src/core/chat.py`) decomposes into:

| Component | Typical Latency | Notes |
|-----------|-----------------|-------|
| Vector Store Load (cold) | 2–5 s | FAISS deserialisation + BM25 index construction |
| Vector Store Load (cached) | <10 ms | `VectorStoreCache` LRU hit (`CACHE_TTL=3600s`, max 10 entries) |
| HyDE Hypothetical Doc | 2–4 s | Single LLM call (`gemini-2.5-flash`, `max_tokens=500`) |
| BM25 Sparse Retrieval | <0.5 s | In-memory computation, `default_tokenizer` regex-based |
| FAISS Dense Retrieval | <0.5 s | `similarity_search_with_relevance_scores(k=30)` |
| Hybrid Fusion + MMR | <0.1 s | Score normalisation, `MMR_TARGET=12`, `λ=0.5` |
| LLM Final Generation | 10–15 s | Qwen-235B via streaming; first-token ~3 s |
| **Total (cold)** | **~15–25 s** | |
| **Total (warm cache)** | **~12–20 s** | |

Key design choices affecting latency:
- **Category-based routing**: code chunks use `CATEGORY_TOP_K["code"]=5`, text uses `CATEGORY_TOP_K["text"]=2`; higher code budget because code queries dominate usage.
- **Dense multiplier**: `DENSE_K_MULTIPLIER=6` means code retrieval pulls up to 30 candidates before fusion, ensuring recall for specific symbol names.
- **MAX_TOTAL_CANDIDATES=60**: Hard cap prevents excessive MMR computation.

### 5.3.3 Measurement Methodology

The benchmark script (`test/chapter5/03_benchmark_rag_latency.py`) executes:
1. Fixed queries against a pre-built vector store
2. Repeated rounds (default 3) for retrieval-stage timings
3. Single-round HyDE on/off comparison for full-chat latency
4. CSV output for statistical analysis

It performs a graceful exit if the vector store directory is missing or if `OPENROUTER_API_KEY` is not set, avoiding false test failures in CI.

---

## 5.4 Qualitative Quality Assessment

### 5.4.1 Module Partition Quality (Leiden Community Discovery)

The `CommunityEngine` (`docker/src/ingestion/community_engine.py`) applies the Leiden algorithm to a code knowledge graph built by `CodeGraphBuilder` (`docker/src/ingestion/code_graph.py`). The graph treats files, classes, and functions as nodes; import/call/inheritance edges connect them.

**Evaluation criteria** (from `test/chapter5/04_qualitative_rubric.md`):

| Criterion | Description |
|-----------|-------------|
| A1. Module count | Neither excessive fragmentation (>20) nor under-segmentation (<3) |
| A2. UI / Logic separation | Frontend components vs. backend logic placed in distinct communities |
| A3. Infrastructure isolation | Config/CI/deployment files not mixed with core business modules |
| A4. Summary accuracy | Community summaries correctly describe primary responsibilities |
| A5. Cross-module references | Wiki chapter cross-references reflect actual code dependencies |

For **reactive-resume** (TypeScript frontend-heavy), the Leiden algorithm is expected to separate UI components (React), resume data models, and API integration layers into distinct communities. For **MinerU** (Python document processing), the parser modules, conversion engines, and CLI entry points should form natural clusters.

### 5.4.2 Mermaid Diagram Accuracy

Each generated Wiki section may include a Mermaid code block (class diagram, flowchart, or sequence diagram). Evaluation follows:

| Criterion | Description |
|-----------|-------------|
| B1. Syntax validity | Renders correctly in mermaid.live or GitHub Markdown |
| B2. Identifier accuracy | Class/function names exist in the actual codebase |
| B3. Dependency direction | Arrow directions match real call/inheritance relationships |
| B4. No fabrication | No phantom entities not present in the repository |
| B5. Diagram type match | Selected diagram type suits the described relationship |

A helper script for bulk extraction of Mermaid blocks from generated JSON and a quick bracket-balance checker are provided in the rubric document.

### 5.4.3 Content Accuracy

Manual review assesses:
- **Technical terminology correctness** — framework/library names spelled correctly
- **File path accuracy** — referenced paths exist in the repository
- **Logical coherence** — no self-contradictions across sections
- **Hallucination rate** — descriptions of non-existent features/files
- **Depth adequacy** — core modules explained with sufficient detail, not mere listings

The structured scoring rubric (`test/chapter5/04_qualitative_rubric.md`) provides a 1–5 scale across three dimensions (Module Partition, Mermaid Accuracy, Content Accuracy) for each test repository.

---

## 5.5 Comparison of Retrieval Strategies

### 5.5.1 Retrieval Architecture Overview

The system implements three retrieval strategies in `docker/src/core/retrieval.py`:

1. **Dense Vector Retrieval** — FAISS index with `baai/bge-m3` embeddings; `similarity_search_with_relevance_scores` returns cosine-similarity ranked candidates.

2. **Sparse BM25 Retrieval** — Custom `SparseBM25Index` built on a regex tokeniser (`[A-Za-z0-9_\u4e00-\u9fff]+`); classical BM25 scoring with `k1=1.5`, `b=0.75`.

3. **Community-First Two-Stage Retrieval** — `CommunityFirstRetriever` first matches the query to relevant Leiden communities via BM25 on community summaries, then searches within those communities for specific code chunks.

### 5.5.2 Hybrid Fusion in Production Path

The primary RAG path (`_gather_hybrid_candidates` in `chat.py`) uses strategies 1 and 2 in a **hybrid fusion** configuration:

```
final_score = 0.6 × dense_score_normalized + 0.4 × sparse_score_normalized
```

This is followed by **Maximal Marginal Relevance (MMR)** selection (`mmr_select` with `λ=0.5`, target 12 documents) to balance relevance and diversity.

**Why hybrid outperforms single-strategy retrieval:**

- **Sparse retrieval excels at exact symbol matching**: Queries like "Where is `CATEGORY_TOP_K` defined?" or "What does `_safe_filename` do?" benefit from BM25's term-frequency weighting, which directly matches variable names, configuration keys, and function identifiers.

- **Dense retrieval captures semantic similarity**: Queries like "How does error handling work?" match documents discussing exception patterns even without keyword overlap.

- **Fusion provides best-of-both**: The 0.6/0.4 weighting reflects the empirical observation that most user queries have a semantic component (benefiting from dense) but also reference specific code tokens (benefiting from sparse).

### 5.5.3 Community-First Retrieval: Status and Analysis

The `CommunityFirstRetriever` class is **fully implemented** in `retrieval.py` with a complete `hybrid_retrieve` method that:
1. Runs community-level BM25 to identify relevant functional modules
2. Performs intra-community document retrieval
3. Fuses results with dense vector scores

However, this retriever is **not yet integrated into the main RAG path** (`answer_question` / `_gather_hybrid_candidates`). The production pipeline uses the simpler hybrid fusion of dense + sparse without community awareness.

**Reasons for non-integration**:
- The community graph requires a prior generation pass (currently only available during Wiki creation, not persisted for RAG queries)
- Adding community context increases cold-start latency, as community mappings must be loaded alongside the vector store
- The current hybrid fusion already provides satisfactory retrieval quality for typical code-understanding queries

This represents a **technical debt** item — the infrastructure is built but awaiting the persistence layer to make community graphs available at query time. Integration would benefit queries about architectural relationships ("How do the modules interact?") where community-level context provides valuable priming.

### 5.5.4 HyDE Enhancement

The optional HyDE (Hypothetical Document Embeddings) step (`_generate_hyde_document`) bridges the query–document semantic gap:

1. The user's question is sent to a fast LLM (Gemini 2.5 Flash)
2. A hypothetical answer is generated (~500 tokens)
3. This hypothetical document replaces the raw question for dense retrieval

HyDE is most effective for conceptual questions ("What design patterns does this project use?") where the user's phrasing differs significantly from code-level documentation. For precise symbol lookups, BM25 already captures the relevant terms, so HyDE's benefit is marginal.

---

# Chapter 6: Conclusion and Critical Review

## 6.1 Summary of Achievements

This project set out to build an **automated Wiki generation and interactive code understanding system** for GitHub repositories. Against the original objectives, the following deliverables have been completed:

| Objective | Deliverable | Status |
|-----------|------------|--------|
| Web-based frontend for repository exploration | Next.js SPA with Supabase Auth, i18n, dark/light theme | ✅ Complete |
| Backend API for task management | FastAPI service with async task queue, progress tracking, cancellation | ✅ Complete |
| Automated Wiki generation pipeline | Clone → AST → GraphRAG → Structure/Content → R2 + Supabase | ✅ Complete |
| RAG-based Q&A | HyDE + Hybrid retrieval + MMR + Streaming SSE | ✅ Complete |
| Agent-based deep code understanding | Multi-turn tool-calling agent (rag_search, code_graph, file_read, repo_map) | ✅ Complete |
| Code analysis infrastructure | Tree-sitter AST, NetworkX knowledge graph, Leiden community detection | ✅ Complete |
| Multi-model orchestration | OpenRouter gateway with task-specific model assignment | ✅ Complete |
| Persistent storage architecture | Fly Volume + R2 + Supabase + vector store caching | ✅ Complete |

The system successfully processes repositories of varying scale (from ~20 files to 300+), generating structured Wiki documentation in 14–25 minutes with concurrent content production.

## 6.2 Critical Review

### 6.2.1 Technical Challenges and Solutions

**Large-scale AST Parsing — Memory and Performance**

The Tree-sitter based parser (`ts_parser.py`) must handle repositories with hundreds of files across multiple languages. Key challenges included:
- **Language pack compatibility**: The transition from `tree-sitter-languages` to `tree-sitter-language-pack` required dual-backend support (lines 7–19 of `ts_parser.py`), as the older package breaks with tree-sitter 0.22+.
- **Parser API migration**: tree-sitter 0.22 changed `Parser(language)` from `Parser().set_language(language)`, requiring a try/except fallback (lines 82–88).
- **Minimal file handling**: Files without meaningful function/class definitions trigger a whole-file fallback (`node_type="file"`) to avoid losing context.

**LLM Hallucination — Path Fabrication**

A recurring issue during Wiki content generation was the LLM fabricating file paths that do not exist in the repository. This was addressed through `valid_file_list` constraints in the prompt:
- The `WIKI_SECTION_PROMPT` (in `docker/src/prompts.py`) includes the actual file tree and explicit instructions to reference only existing files.
- The `generate_wiki_structure` function in `struct_gen.py` builds the file tree with `generate_file_tree` using filter rules from `repo_config.json`, ensuring the LLM sees only permissible paths.
- Post-generation validation checks that referenced files exist in the repository snapshot.

**Concurrent Generation Stability**

`WikiContentGenerator.generate()` uses `ThreadPoolExecutor` with configurable `max_concurrency` (default 3, from `repo_config.json`). Challenges included:
- **Filename collision**: Multiple sections could map to the same safe filename; the `_build_filename_map` method with normalisation and suffix numbering resolves this.
- **Partial failure isolation**: One section's LLM timeout must not block or corrupt other sections. The `as_completed` pattern with per-future exception handling ensures this.
- **Progress tracking**: Thread-safe progress accumulation via callback provides accurate Supabase status updates.

### 6.2.2 Engineering Considerations

**Task-Level Directory Isolation**

Each generation task operates in its own working directory (`TASK_WORK_ROOT/<task_id>/`), containing:
- `wiki_structure.json` — generated table of contents
- `wiki_section_json/` — per-section content files

This design prevents race conditions when multiple tasks run concurrently and simplifies cleanup. The `cleanup_local_files` function removes the entire task directory after successful R2 upload, while preserving persistent repository clones in `REPO_STORE_PATH`.

**Failure Recovery: RAG Indexing Retry**

The pipeline is designed so that **Wiki upload (R2) and RAG indexing are decoupled**. If RAG indexing fails (e.g., embedding API timeout), the Wiki is still marked as completed and accessible. A background retry mechanism (`_background_retry_rag_indexing`) schedules up to 3 retries with exponential backoff (30s, 120s, 300s), each cloning the repository independently to avoid dependencies on cleaned-up temporary files.

**User Cancellation**

Running tasks can be cancelled via `POST /task/{id}/cancel`. The `_task_marked_cancelled_by_user` check prevents race conditions where a background task attempts to overwrite a user-cancelled status with "completed".

### 6.2.3 Technical Debt Analysis

The following features are implemented but not fully integrated into the production path:

1. **`CommunityFirstRetriever`** — Fully implemented in `retrieval.py` with two-stage retrieval (community matching → intra-community search) and hybrid fusion. Not connected to `answer_question` because community graph persistence for query-time access is not yet built. The retriever would benefit queries about module relationships and architectural patterns.

2. **Embedding model flexibility** — The `ai_client_factory.py` abstracts model selection, but the embedding model (`baai/bge-m3`) is hardcoded in the knowledge base loader. Switching embeddings requires rebuilding all vector stores.

3. **Incremental updates** — The current pipeline always regenerates the entire Wiki from scratch. Commit-diff-based incremental updates (detecting changed files and regenerating only affected sections) would significantly reduce regeneration time for actively maintained repositories.

## 6.3 Learning Experiences

### 6.3.1 Multi-Model Dispatch via OpenRouter

One of the most impactful architectural decisions was using **OpenRouter as a unified LLM gateway** rather than directly integrating individual provider APIs. This enabled:

- **Task-specific model selection**: Structure generation uses Gemini 2.5 Flash (fast, good at structured output), while content generation uses Qwen 3.5 Flash (strong Markdown/code generation), and final RAG answers use Qwen 235B (highest quality).
- **Transparent failover**: OpenRouter's routing handles rate limits and provider outages without application-level retry logic for model availability.
- **Cost optimisation**: Cheaper models handle high-volume tasks (HyDE, section generation), while premium models are reserved for user-facing answers.
- **Single API key management**: Instead of managing keys for 3+ providers, a single `OPENROUTER_API_KEY` suffices.

The configuration in `repo_config.json` makes model assignment declarative:
```json
"ai_models": {
    "provider": "openrouter",
    "models": {
        "hyde_generation": "google/gemini-2.5-flash",
        "wiki_structure": "google/gemini-2.5-flash",
        "wiki_content": "qwen/qwen3.5-flash-02-23",
        "rag_answer": "qwen/qwen3-235b-a22b-2507"
    }
}
```

### 6.3.2 Vector Store Caching

The `VectorStoreCache` class in `chat.py` implements process-level LRU caching with TTL (1 hour, max 10 entries). This was introduced after observing that FAISS deserialisation + BM25 index construction added 2–5 seconds to every query. With caching, subsequent queries to the same repository see <10ms load times.

### 6.3.3 GraphRAG Integration

Combining the Leiden community detection algorithm with LLM-based summarisation (GraphRAG pattern) proved effective for generating meaningful Wiki structures. The community summaries provide natural chapter boundaries, while the knowledge graph ensures that code dependencies guide the narrative flow rather than simple directory structure.

## 6.4 Future Work

### 6.4.1 Incremental Wiki Updates

The current regeneration model (full pipeline re-run) is wasteful for repositories with minor changes. A commit-diff-based approach would:
1. Detect changed files via `git diff`
2. Identify affected communities in the knowledge graph
3. Regenerate only the Wiki sections mapped to those communities
4. Merge updated sections into the existing Wiki structure

This would reduce update time from 14–25 minutes to potentially 2–5 minutes for typical commits.

### 6.4.2 Automated Mermaid Validation

Currently, generated Mermaid diagrams are not validated before serving. Future improvements include:
- **Syntax validation**: Parse Mermaid code with the mermaid-js CLI or a lightweight grammar checker before storing
- **Entity cross-reference**: Automatically verify that class/function names in diagrams exist in the codebase
- **Rendering fallback**: Serve a text-based alternative when Mermaid rendering fails in the browser

### 6.4.3 Community-Aware RAG Integration

Completing the integration of `CommunityFirstRetriever` into the production RAG path requires:
1. Persisting the community graph (community assignments + summaries) alongside the vector store during `run_rag_indexing`
2. Loading community data in `_load_vector_stores` when available
3. Using community context as a retrieval pre-filter for architectural queries

### 6.4.4 Persistent Task Queue

The current implementation uses `asyncio.create_task` for background processing, which does not survive server restarts. Migration to a persistent task queue (Redis + Celery, or similar) would enable:
- Task resumption after deployment
- Horizontal scaling across multiple workers
- Better observability via queue monitoring

---

## References

> Note: Reference numbers [1]–[20] correspond to the outline in `report.md`. Final verification of titles, authors, and URLs should be done against the IR II submission and official documentation.

- [1]–[2] IR II experimental data and intermediate report
- [3] FYP Assessment Guidelines
- [4] Fly.io Documentation — https://fly.io/docs/
- [5] Vercel Documentation — https://vercel.com/docs
- [6] PyTest Documentation — https://docs.pytest.org/
- [7] `docker/src/wiki/content_gen.py` — ThreadPoolExecutor concurrent generation
- [8] IR II timing measurements (14–25 min, 30–50 pages)
- [9] `docker/src/core/chat.py` — RAG response path analysis
- [10] `docker/src/core/retrieval.py` — Hybrid retrieval implementation
- [11] RAG audit report findings on hybrid vs. single-strategy retrieval
- [12] FYP Final Report Guidelines — Critical Review section requirements
- [13] `docker/src/core/wiki_pipeline.py` — Pipeline orchestration
- [14] `docker/scripts/api.py` — FastAPI endpoint definitions
- [15] `docker/src/ingestion/ts_parser.py` — Tree-sitter AST parsing
- [16] `docker/src/prompts.py` — `valid_file_list` path constraints in prompts
- [17] `docker/src/core/wiki_pipeline.py` — Task isolation and retry mechanisms
- [18] `docker/src/core/retrieval.py` — `CommunityFirstRetriever` (unintegrated)
- [19] Git diff-based incremental update concept
- [20] Mermaid.js Documentation — https://mermaid.js.org/
