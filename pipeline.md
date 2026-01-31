# Detailed Methodology and Implementation

## 1. Repository Ingestion

### Clone Strategy
The system uses **GitPython** (`git.Repo.clone_from`) to clone the target repository.
- **Mechanism**: It clones the full repository into a secure temporary directory (`tempfile.mkdtemp()`).
- **Policy**: Currently, it performs a full clone (not shallow) to ensure complete history and context availability, though the analysis primarily focuses on the `HEAD` state.

### Ignore Rules
To maintain relevance and reduce noise, the ingestion process applies strict filtering:
- **Configuration**: Uses `repo_config.json` to define `excluded_dirs` and `excluded_files`.
- **Hardcoded Ignores**:
  - Directories: `node_modules`, `__pycache__`, `venv`, `env`, `.git`.
  - Files: Binary files are detected by reading the first 1024 bytes and checking for null bytes (`\x00`).
  - Hidden files/folders (starting with `.`) are ignored by default.
- **Thresholds**: Large files or specific binary types are automatically excluded during the traversal phase.

### Language Identification & Tree-sitter Parser Selection
- **Extension Mapping**: The system maintains a mapping (`EXTENSION_TO_LANGUAGE`) between file extensions (e.g., `.py`, `.ts`, `.rs`) and Tree-sitter language names.
- **Parser Loading**:
  - Uses `tree_sitter_language_pack` or `tree_sitter_languages` as a fallback.
  - Dynamically loads the appropriate parser based on the detected file extension during the processing of each file.

## 2. RepoMap Construction

### RepoMap Output Format
The RepoMap is generated as a structured text summary, designed to fit into LLM context windows.
- **Structure**: A tree-like textual representation showing the file hierarchy.
- **Content**: Annotated with "tags" (definitions and references) extracted from the code.
- **Ranking**: Files and symbols are ranked to highlight the most important elements.

### Key Files Selection Heuristics
- **Source Files**: Identified via `find_src_files` which filters out non-source directories.
- **Ranking Algorithm**: Uses a **PageRank-based approach** on the graph of definitions and references.
  - Important files (high rank) are prioritized in the map.
  - Files explicitly mentioned in chat or currently open (if applicable) receive higher weight.
- **Tag Extraction**: Uses Tree-sitter queries (`.scm` files) to extract:
  - `def`: Function and class definitions.
  - `ref`: References/calls to these definitions.

## 3. Code Parsing & Chunking

### Tree-sitter AST Traversal
- **Parser**: `TreeSitterParser` (`src/ingestion/ts_parser.py`) parses code into an Abstract Syntax Tree (AST).
- **Traversal**: Recursively visits nodes to identify "interesting" structures based on language-specific types (e.g., `function_definition`, `class_declaration`).

### Function/Class Chunking Boundaries
- **Strategy**: Instead of arbitrary line splitting, the system respects semantic boundaries.
- **Extraction**:
  - **Functions/Classes**: Extracts the full byte range of the node (start_byte to end_byte).
  - **Fallback**: If no structural chunks are found (e.g., flat scripts), the entire file is treated as a single chunk.
- **Granularity**: Top-level definitions are preferred to avoid excessive fragmentation.

### Chunk Metadata
Each chunk is wrapped in a `Document` object with rich metadata:
- `source`: Absolute file path.
- `start_line` / `end_line`: Location in the source file.
- `node_type`: AST node type (e.g., `function_definition`).
- `name`: Identifier name (e.g., function name), extracted from the child `identifier` node.

## 4. CodeGraphBuilder

### Graph Nodes & Edges
- **Library**: Uses **NetworkX** (`nx.DiGraph`) for graph construction.
- **Nodes**:
  - **File Nodes**: Represent source files (`type="file"`).
  - **Entity Nodes**: Represent Classes and Functions (`type="class"`, `type="function"`).
- **Edges**:
  - `contains`: Directed edge from File to Entity (File A contains Class B).
  - `calls`: Directed edge between Functions/Classes representing invocation.

### Dependency/Call Extraction
- **Heuristic Matching**:
  - Iterates through AST nodes to find `call_expression` or `call` types.
  - Extracts the called identifier name.
  - Performs a name-based search in the existing graph nodes to establish `calls` edges.
  - *Note*: This is a lightweight, heuristic approach (name matching) rather than full compiler-level symbol resolution.

### Graph Storage
- **Format**: JSON.
- **Serialization**: Uses `nx.node_link_data` to serialize the graph structure (nodes and links) into a standard JSON format compatible with frontend visualization libraries.

## 5. CommunityEngine (Leiden)

### Community Detection
- **Algorithm**: **Leiden Algorithm** (`leidenalg`).
- **Implementation**:
  - Converts the NetworkX directed graph to an `igraph` undirected graph.
  - Applies `leidenalg.find_partition` with `ModularityVertexPartition`.
- **Output**: A mapping of `Community ID -> List[Node IDs]`. Nodes within a community are densely connected, representing a logical functional module.

### Community Summarization
- **Process**:
  - For each community, representative nodes (Files/Classes) are selected.
  - **Prompt**: An LLM (DeepSeek) is prompted with the list of entities.
  - **Objective**: Generate a concise (<100 words) summary describing the business function of this code cluster.
- **Output**: A dictionary mapping `Community ID -> Summary Text`.

## 6. Wiki Planning & Content Generation

### Wiki Structure Planning (`wiki_structure.json`)
- **Generator**: `src/wiki/struct_gen.py` using **Qwen-max** model.
- **Inputs**:
  - File Tree.
  - README content.
  - RepoMap context.
  - Community Summaries (from GraphRAG).
  - Valid File List (for hallucinations prevention).
- **Output Schema** (JSON):
  ```json
  {
    "title": "Project Name",
    "description": "...",
    "toc": [
      {
        "id": "unique_id",
        "title": "Section Title",
        "files": ["relative/path/to/file.py"],
        "children": [...]
      }
    ]
  }
  ```

### Section Content Generation
- **Generator**: `src/wiki/content_gen.py` using **Qwen** model.
- **Process**:
  - Iterates through the TOC.
  - **Context Assembly**: Aggregates code snippets from linked files.
  - **Source Binding**: Incorporates "Logical Dependencies" derived from the Code Graph (e.g., "File A calls File B").
- **Schema**:
  - **Content**: Markdown text.
  - **Diagrams**: Mermaid.js class/flow diagrams.
  - **Sources**: Explicit references to file paths at the start of sections.

## 7. Embedding & Indexing

### Embedding Batch Processing
- **Model**: `text-embedding-3-small` (OpenAI).
- **Batching**: Documents are processed in batches of **300** (`EMBEDDING_BATCH_SIZE`) to optimize API throughput and handle rate limits.

### Indexing Strategy
- **Vector Index**: **FAISS** (Facebook AI Similarity Search).
  - Used for dense vector retrieval.
  - Saved locally via `db.save_local`.
- **Sparse Index**: **BM25** (Custom Implementation `SparseBM25Index`).
  - **Tokenization**: Regex-based `[A-Za-z0-9_\u4e00-\u9fff]+` to support multilingual (English/Chinese) and code tokens.
  - In-memory structure (Term Frequencies, IDF).

### Persistence
- **Path Organization**: Indexes are stored under `vector_stores/{repo_name}/`.
- **Storage**: On deployment, these are persisted to a volume (e.g., `/data` on Fly.io) to survive restarts.

## 8. Hybrid Retrieval + Rerank

### Community-First Retrieval
1.  **Macro Search**: Use BM25 to search against **Community Summaries**. Identify the most relevant functional clusters (Communities) for the user query.
2.  **Micro Search**: Restrict the document search scope to the nodes (files/chunks) within the top-ranked communities.

### Scoring & Fusion
- **Candidates**: `RankedCandidate` objects hold both `dense_score` (Vector) and `sparse_score` (BM25).
- **Normalization**: Scores are normalized to [0, 1] range using Min-Max scaling (`normalize_scores`).
- **Fusion**: (Implicitly) The system supports weighted combination, though currently often relies on the "Community First" two-stage filtering or specific retriever selection.

### MMR (Maximal Marginal Relevance)
- **Formula**:
  $$ MMR = \arg\max_{d_i \in R} [ \lambda \cdot Sim_1(d_i, q) - (1-\lambda) \cdot \max_{d_j \in S} Sim_2(d_i, d_j) ] $$
- **Implementation**:
  - Calculates Cosine Similarity between Query and Candidate ($Sim_1$).
  - Calculates Cosine Similarity between Candidate and already Selected candidates ($Sim_2$ - Penalty).
  - Iteratively selects the candidate that maximizes relevance while minimizing redundancy.

## 9. API Design

### Key Endpoints (Task-Based)
The API follows an asynchronous task model via `FastAPI`.
- **POST /generate**: Accepts `repo_url`. Returns `task_id`.
- **GET /status/{task_id}**: Returns `TaskStatusResponse`.
- **POST /chat**: Accepts `ChatRequest` (question, repo_url). Returns `ChatResponse`.

### State Machine
- `PENDING`: Task accepted.
- `PROCESSING`: Steps executing (Clone -> Graph -> Wiki).
- `COMPLETED`: Result available (JSON URLs).
- `FAILED`: Error occurred.

### Error Handling
- **TaskInfo**: Captures exception messages in the `error` field.
- **Logs**: Standard output logging for monitoring (Fly.io logs).

## 10. Deployment

### Docker Configuration
- **Base Image**: `python:3.11-slim` for minimal footprint.
- **Dependencies**: Installs `git` (system) and Python requirements.
- **Context**: Copies full source code.
- **Entrypoint**: Runs `scripts/api.py`.

### Fly.io Configuration
- **Volume Mount**:
  - `source = "rag_data"`
  - `destination = "/data"`
  - Purpose: Persist Vector Stores and generated Wiki JSONs.
- **Resources**: Configured for 1GB+ RAM to handle graph operations and FAISS.

### Supabase Integration
- **Role**: Used for storing chat history and user sessions.
- **Schema**: `migrations/001_chat_messages.sql` defines tables for `chats` and `messages`.
