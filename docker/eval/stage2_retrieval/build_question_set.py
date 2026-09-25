"""Builds the N-question, file-level ground-truth benchmark over M repos for
Stage 2 (Recall@5 ablation) and Stage 3 (citation validity).

Ground truth comes from each repo's own `code_graph.json` (already produced,
free, by Stage 1) -- no separate ingestion or retrieval call needed here:

- ~60% deterministic: templated "where is `X` defined" questions, ground
  truth = the exact file_path from the graph node. Free, high-precision.
- ~40% semantic: a cheap model (`hyde_generation`, already configured) writes
  a natural-language question about what a sampled function/class does,
  without echoing its name verbatim; ground truth = that node's file_path.

Usage (from `docker/eval/`):
    python -m stage2_retrieval.build_question_set --num-repos 8 --per-repo 6
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from common import bootstrap  # noqa: E402,F401
from common import llm_usage_tracker as tracker  # noqa: E402
from common.config import GENERATION_RESULTS_PATH, QUESTION_SET_PATH  # noqa: E402

tracker.install()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from src.clients import get_llm  # noqa: E402

_SEED = 42
_RAW_CODE_MAX_CHARS = 1200
_MIN_RAW_CODE_CHARS = 40

_SEMANTIC_SYSTEM_PROMPT = (
    "You write short, natural developer questions about a piece of source code, the way a "
    "real engineer unfamiliar with the codebase would ask them (e.g. 'how does this project "
    "handle retries when a request fails?'). Never mention the exact function/class/variable "
    "name verbatim, never quote code back, and never mention the file name. Output only the "
    "single question, no preamble."
)


def _load_completed_repos() -> list[dict[str, Any]]:
    if not GENERATION_RESULTS_PATH.exists():
        raise SystemExit(f"{GENERATION_RESULTS_PATH} not found -- run Stage 1 first.")
    rows = []
    for line in GENERATION_RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "completed" and row.get("vector_store_path"):
            rows.append(row)
    return rows


def _select_repos(rows: list[dict[str, Any]], num_repos: int) -> list[dict[str, Any]]:
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_tier.setdefault(r.get("tier", "unknown"), []).append(r)
    for bucket in by_tier.values():
        random.Random(_SEED).shuffle(bucket)

    selected: list[dict[str, Any]] = []
    tiers = list(by_tier.keys())
    i = 0
    while len(selected) < num_repos and any(by_tier.values()):
        tier = tiers[i % len(tiers)]
        if by_tier[tier]:
            selected.append(by_tier[tier].pop())
        i += 1
        if i > num_repos * len(tiers) + len(tiers):
            break
    return selected[:num_repos]


def _load_graph_nodes(vector_store_path: str) -> list[dict[str, Any]]:
    graph_file = Path(vector_store_path) / "code_graph.json"
    if not graph_file.is_file():
        print(f"  ! no code_graph.json at {graph_file}, skipping")
        return []
    data = json.loads(graph_file.read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    return [
        n
        for n in nodes
        if n.get("type") in ("function", "class")
        and n.get("file_path")
        and n.get("raw_code")
        and _MIN_RAW_CODE_CHARS <= len(n["raw_code"]) <= 6000
    ]


def _generate_semantic_question(node: dict[str, Any]) -> str:
    code = node["raw_code"][:_RAW_CODE_MAX_CHARS]
    human = f"Source code:\n```\n{code}\n```\n\nWrite one natural question a developer might ask about this."
    llm = get_llm("hyde_generation", temperature=0.4, max_tokens=120)
    response = llm.invoke(
        [SystemMessage(content=_SEMANTIC_SYSTEM_PROMPT), HumanMessage(content=human)]
    )
    text = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    return text.strip().strip('"')


def build_questions_for_repo(
    row: dict[str, Any], per_repo: int, rng: random.Random
) -> list[dict[str, Any]]:
    nodes = _load_graph_nodes(row["vector_store_path"])
    if not nodes:
        return []
    rng.shuffle(nodes)

    n_det = max(1, round(per_repo * 0.6))
    n_sem = max(0, per_repo - n_det)
    det_nodes = nodes[:n_det]
    sem_nodes = nodes[n_det : n_det + n_sem]

    questions: list[dict[str, Any]] = []
    for node in det_nodes:
        kind_phrase = "class" if node["type"] == "class" else "function"
        question = f"Which file defines the {kind_phrase} `{node['qualified_name']}`?"
        questions.append(
            {
                "repo_url": row["repo_url"],
                "repo_id": row["repo_id"],
                "question": question,
                "ground_truth_files": [node["file_path"]],
                "source": "deterministic",
                "node_type": node["type"],
                "node_name": node["qualified_name"],
            }
        )

    for node in sem_nodes:
        try:
            question = _generate_semantic_question(node)
        except Exception as exc:
            print(
                f"  ! semantic question generation failed for {node.get('qualified_name')}: {exc}"
            )
            continue
        if not question:
            continue
        questions.append(
            {
                "repo_url": row["repo_url"],
                "repo_id": row["repo_id"],
                "question": question,
                "ground_truth_files": [node["file_path"]],
                "source": "semantic",
                "node_type": node["type"],
                "node_name": node["qualified_name"],
            }
        )

    return questions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--num-repos",
        type=int,
        default=8,
        help="M: how many repos to draw questions from",
    )
    parser.add_argument(
        "--per-repo",
        type=int,
        default=6,
        help="Questions per repo (~60%% deterministic / 40%% semantic)",
    )
    args = parser.parse_args()

    rows = _load_completed_repos()
    selected = _select_repos(rows, args.num_repos)
    print(
        f"Selected {len(selected)} repo(s) for the question set: {[r['repo_url'] for r in selected]}"
    )

    rng = random.Random(_SEED)
    all_questions: list[dict[str, Any]] = []
    for row in selected:
        print(f"\n=== {row['repo_url']} ===")
        qs = build_questions_for_repo(row, args.per_repo, rng)
        for i, q in enumerate(qs):
            q["question_id"] = f"{row['repo_id']}::{i}"
        all_questions.extend(qs)
        print(f"  {len(qs)} question(s) generated")

    with QUESTION_SET_PATH.open("w", encoding="utf-8") as out:
        for q in all_questions:
            out.write(json.dumps(q, ensure_ascii=False) + "\n")

    print(
        f"\nWrote {len(all_questions)} questions across {len(selected)} repos to {QUESTION_SET_PATH}"
    )
    print(f"Question-generation LLM cost: ${tracker.total_cost_usd():.4f}")
    print(
        "Recommend spot-checking ~10% of these by eye before trusting recall numbers."
    )


if __name__ == "__main__":
    main()
