"""Stage 2: file-level Recall@5 for three retrieval configurations, called as
direct Python functions -- bypassing the chat LLM entirely, so this stage
costs ~$0 beyond trivial query-embedding calls (which Stage 1's own indexing
already paid for; only the query-side embedding is new here).

Configs:
  A. dense-only        -- qdrant_search_category(sparse_weight=0)
  B. hybrid, no HyDE/MMR -- qdrant_search_category(default 0.6/0.4 weights)
  C. full production    -- RAGSearchEngine.search() (hybrid + HyDE + MMR,
                            exactly what the live chat agent uses)

Usage (from `docker/eval/`):
    python -m stage2_retrieval.run_retrieval_eval
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from common import bootstrap  # noqa: E402,F401
from common.config import QUESTION_SET_PATH, RETRIEVAL_RESULTS_PATH  # noqa: E402

from src.chat.tools.rag_tool import RAGSearchEngine  # noqa: E402
from src.core.retrieval import RankedCandidate, qdrant_search_category  # noqa: E402
from src.ingestion.vector_store import get_qdrant_client  # noqa: E402
from src.paths import REPO_STORE_ROOT, VECTOR_STORE_ROOT  # noqa: E402

TOP_K = 5
RAW_K = 15
CATEGORIES = ("code", "text")


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _to_repo_relative(source: str, repo_id: str) -> str:
    if not source:
        return ""
    p = source
    for prefix in ("code:", "text:"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    repo_root = str((REPO_STORE_ROOT / repo_id).resolve())
    try:
        resolved = str(Path(p).resolve())
    except OSError:
        resolved = p
    if resolved.startswith(repo_root):
        resolved = resolved[len(repo_root) :].lstrip("/\\")
    return _normalize_rel(resolved)


def _dedupe_to_top_files(rel_paths: list[str], k: int) -> list[str]:
    seen: list[str] = []
    for p in rel_paths:
        if p and p not in seen:
            seen.append(p)
        if len(seen) >= k:
            break
    return seen


def _config_dense_only(client, repo_id: str, query: str) -> list[str]:
    candidates: list[RankedCandidate] = []
    for category in CATEGORIES:
        candidates.extend(
            qdrant_search_category(
                client,
                category,
                repo_id,
                query,
                dense_k=RAW_K,
                sparse_k=RAW_K,
                dense_weight=1.0,
                sparse_weight=0.0,
            )
        )
    candidates.sort(key=lambda c: c.final_score, reverse=True)
    rel_paths = [
        _to_repo_relative(c.doc.metadata.get("source", ""), repo_id) for c in candidates
    ]
    return _dedupe_to_top_files(rel_paths, TOP_K)


def _config_hybrid_no_hyde_mmr(client, repo_id: str, query: str) -> list[str]:
    candidates: list[RankedCandidate] = []
    for category in CATEGORIES:
        candidates.extend(
            qdrant_search_category(
                client,
                category,
                repo_id,
                query,
                dense_k=RAW_K,
                sparse_k=RAW_K,
                dense_weight=0.6,
                sparse_weight=0.4,
            )
        )
    candidates.sort(key=lambda c: c.final_score, reverse=True)
    rel_paths = [
        _to_repo_relative(c.doc.metadata.get("source", ""), repo_id) for c in candidates
    ]
    return _dedupe_to_top_files(rel_paths, TOP_K)


def _config_full_production(repo_id: str, query: str) -> list[str]:
    engine = RAGSearchEngine(vector_store_path=str(VECTOR_STORE_ROOT / repo_id))
    _, artifact = engine.search(query, top_k=RAW_K)
    rel_paths = [
        _to_repo_relative(r.get("source", ""), repo_id)
        for r in artifact.get("results", [])
    ]
    return _dedupe_to_top_files(rel_paths, TOP_K)


def _is_hit(top_files: list[str], ground_truth_files: list[str]) -> bool:
    gt = {_normalize_rel(g) for g in ground_truth_files}
    return any(_normalize_rel(p) in gt for p in top_files)


def main() -> None:
    if not QUESTION_SET_PATH.exists():
        raise SystemExit(
            f"{QUESTION_SET_PATH} not found -- run stage2_retrieval.build_question_set first."
        )

    client = get_qdrant_client()
    questions = [
        json.loads(l)
        for l in QUESTION_SET_PATH.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    print(f"Evaluating {len(questions)} questions across 3 retrieval configs...")

    hits = defaultdict(int)
    per_repo_hits = defaultdict(lambda: defaultdict(int))
    per_repo_total = defaultdict(int)

    with RETRIEVAL_RESULTS_PATH.open("w", encoding="utf-8") as out:
        for q in questions:
            repo_id = q["repo_id"]
            query = q["question"]
            gt = q["ground_truth_files"]

            results = {
                "dense_only": _config_dense_only(client, repo_id, query),
                "hybrid_no_hyde_mmr": _config_hybrid_no_hyde_mmr(
                    client, repo_id, query
                ),
                "full_production": _config_full_production(repo_id, query),
            }

            row = {
                **q,
                "top5_by_config": results,
                "hit_by_config": {
                    name: _is_hit(files, gt) for name, files in results.items()
                },
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

            per_repo_total[repo_id] += 1
            for name, hit in row["hit_by_config"].items():
                if hit:
                    hits[name] += 1
                    per_repo_hits[repo_id][name] += 1

    n = len(questions)
    print(f"\n=== Recall@{TOP_K} over {n} questions ===")
    for name in ("dense_only", "hybrid_no_hyde_mmr", "full_production"):
        pct = 100.0 * hits[name] / n if n else 0.0
        print(f"  {name:22s}: {hits[name]}/{n} = {pct:.1f}%")

    print(f"\nResults written to {RETRIEVAL_RESULTS_PATH}")


if __name__ == "__main__":
    main()
