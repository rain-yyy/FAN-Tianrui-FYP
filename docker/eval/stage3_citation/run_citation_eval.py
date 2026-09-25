"""Stage 3: real `/chat` turns (the actual `chat_agent` model, `openai/gpt-5.1`
per `repo_config.json` -- the most expensive stage per call) + citation
extraction and validation.

There is no structured citation field anywhere in this codebase (`sources` is
tool-artifact-derived, not parsed from the answer -- see CLAUDE.md's chat
section); this script builds the missing extractor: regex over the answer
text for the `file_path:line_start-line_end` convention the system prompt
asks the model to use (`docker/src/chat/prompts.py`), then checks each
citation resolves to a real file (and, if a line range is given, a real line
range) in the Stage-1 checkout.

Usage (from `docker/eval/`):
    python -m stage3_citation.run_citation_eval --pilot 5
    python -m stage3_citation.run_citation_eval --num-questions 25
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from common import (
    bootstrap,  # noqa: E402,F401
    manifest,  # noqa: E402
)
from common import llm_usage_tracker as tracker  # noqa: E402
from common.api_client import chat_turn  # noqa: E402
from common.config import (  # noqa: E402
    CITATION_RESULTS_PATH,
    DEFAULT_MAX_COST_USD,
    QUESTION_SET_PATH,
    require_eval_user_id,
)

tracker.install()

from src.paths import REPO_STORE_ROOT  # noqa: E402

# Tolerant match for the `file_path:line_start-line_end` convention the system
# prompt (docker/src/chat/prompts.py) asks the model to cite with -- optional
# backticks, optional line range (single line also allowed).
_CITATION_RE = re.compile(
    r"`?(?P<path>(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]+|[\w.\-]+\.[A-Za-z0-9]+):"
    r"(?P<start>\d+)(?:-(?P<end>\d+))?`?"
)

# Answers frequently name a specific file without a line range (or with a
# non-numeric placeholder like `command.go:...`/`file.py:??-??`, when the
# model doesn't actually know the exact lines) -- that's still a checkable
# claim about *which file* backs the answer, just not a precise location.
# Restricted to recognized source/doc extensions (docker/src/ingestion's
# ALLOWED_SUFFIXES plus markdown) and backtick-wrapped, to avoid matching
# incidental dotted tokens like version numbers or model names.
_CODE_EXTENSIONS = "py|js|ts|tsx|jsx|java|go|rs|cpp|c|h|rb|md"
_FILE_TOKEN_RE = re.compile(
    rf"`(?P<path>(?:[\w.\-]+/)+[\w.\-]+\.(?:{_CODE_EXTENSIONS})|[\w.\-]+\.(?:{_CODE_EXTENSIONS}))\b"
)


def extract_citations(answer: str) -> list[dict[str, Any]]:
    """Precise `file:line` citations, plus a second pass for bare/malformed
    file mentions (same path, no verifiable line range) that the strict
    pattern above misses entirely.
    """
    out = []
    precise_path_spans = []
    for m in _CITATION_RE.finditer(answer):
        start = int(m.group("start"))
        end = int(m.group("end")) if m.group("end") else start
        out.append(
            {
                "raw": m.group(0),
                "path": m.group("path"),
                "line_start": start,
                "line_end": end,
                "kind": "line_range",
            }
        )
        precise_path_spans.append(m.span("path"))

    seen_paths = set()
    for m in _FILE_TOKEN_RE.finditer(answer):
        path_start = m.start("path")
        if any(s0 <= path_start < s1 for s0, s1 in precise_path_spans):
            continue  # already captured above with a real line range
        path = m.group("path")
        if path in seen_paths:
            continue  # count each distinct file once per answer, not per mention
        seen_paths.add(path)
        out.append(
            {
                "raw": m.group(0),
                "path": path,
                "line_start": None,
                "line_end": None,
                "kind": "file_only",
            }
        )
    return out


def validate_citation(repo_root: Path, citation: dict[str, Any]) -> dict[str, Any]:
    path = citation["path"]
    candidates = [path, path.lstrip("./")]
    resolved: Path | None = None
    for cand in candidates:
        p = repo_root / cand
        if p.is_file():
            resolved = p
            break

    if resolved is None:
        return {**citation, "file_valid": False, "line_valid": False}

    if citation["line_start"] is None:
        return {**citation, "file_valid": True, "line_valid": None}

    start, end = citation["line_start"], citation["line_end"]
    try:
        total_lines = sum(1 for _ in resolved.open(encoding="utf-8", errors="replace"))
    except OSError:
        return {**citation, "file_valid": True, "line_valid": None}

    line_valid = 1 <= start <= end <= total_lines
    return {**citation, "file_valid": True, "line_valid": line_valid}


def _load_semantic_questions(num_questions: int) -> list[dict[str, Any]]:
    if not QUESTION_SET_PATH.exists():
        raise SystemExit(
            f"{QUESTION_SET_PATH} not found -- run stage2_retrieval.build_question_set first."
        )
    rows = [
        json.loads(l)
        for l in QUESTION_SET_PATH.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    semantic = [r for r in rows if r.get("source") == "semantic"]
    return semantic[:num_questions]


async def run_one(q: dict[str, Any], user_id: str) -> dict[str, Any]:
    tracker.reset()
    response = await chat_turn(q["question"], q["repo_url"], user_id)
    manifest.add_chat_id(q["repo_url"], response["chat_id"])

    answer = response.get("answer", "")
    citations = extract_citations(answer)
    repo_root = REPO_STORE_ROOT / q["repo_id"]
    validated = [validate_citation(repo_root, c) for c in citations]

    cost = tracker.total_cost_usd()
    row = {
        "question_id": q["question_id"],
        "repo_url": q["repo_url"],
        "question": q["question"],
        "answer": answer,
        "chat_id": response["chat_id"],
        "citations": validated,
        "num_citations": len(validated),
        "any_valid_file": any(c["file_valid"] for c in validated),
        "any_valid_line": any(c.get("line_valid") for c in validated),
        "cost_usd": round(cost, 4),
    }
    print(
        f"  {q['question_id']}: {len(validated)} citation(s), "
        f"{sum(c['file_valid'] for c in validated)} file-valid, cost=${cost:.4f}"
    )
    return row


def _print_summary(rows: list[dict[str, Any]]) -> None:
    all_citations = [c for r in rows for c in r["citations"]]
    precise = [c for c in all_citations if c["kind"] == "line_range"]
    n_answers = len(rows)
    n_with_citation = sum(1 for r in rows if r["num_citations"] > 0)
    n_file_valid = sum(1 for c in all_citations if c["file_valid"])
    n_line_checked = sum(1 for c in precise if c.get("line_valid") is not None)
    n_line_valid = sum(1 for c in precise if c.get("line_valid"))

    print(
        f"\n=== Citation validity over {n_answers} answer(s), {len(all_citations)} citation(s) "
        f"({len(precise)} with a line range, {len(all_citations) - len(precise)} file-only) ==="
    )
    print(f"  answers with >=1 citation (any kind): {n_with_citation}/{n_answers}")
    if all_citations:
        print(
            f"  file-valid (any kind):      {n_file_valid}/{len(all_citations)} = "
            f"{100 * n_file_valid / len(all_citations):.1f}%"
        )
        if n_line_checked:
            print(
                f"  file+line-valid (of `file:line` citations only): "
                f"{n_line_valid}/{n_line_checked} = {100 * n_line_valid / n_line_checked:.1f}%"
            )


async def main_async(args: argparse.Namespace) -> None:
    user_id = require_eval_user_id()
    num_questions = args.pilot or args.num_questions
    questions = _load_semantic_questions(num_questions)
    print(
        f"Running {len(questions)} real /chat turn(s), budget ceiling ${args.max_cost_usd:.2f}"
    )

    cumulative_cost = 0.0
    rows: list[dict[str, Any]] = []
    with CITATION_RESULTS_PATH.open("w", encoding="utf-8") as out:
        for q in questions:
            if cumulative_cost >= args.max_cost_usd:
                print(
                    f"\n[stop] cumulative cost ${cumulative_cost:.2f} reached ceiling "
                    f"${args.max_cost_usd:.2f}; remaining questions left unrun."
                )
                break
            row = await run_one(q, user_id)
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            cumulative_cost += row["cost_usd"]

    _print_summary(rows)
    print(f"\nTotal tracked cost this run: ${cumulative_cost:.4f}")
    print(f"Results written to {CITATION_RESULTS_PATH}")


def recompute_from_existing() -> None:
    """Re-run extraction/validation over already-collected `answer` text --
    zero new `/chat` calls, zero new cost. Use this after changing
    extract_citations()/validate_citation() instead of re-spending on Stage 3.
    """
    if not CITATION_RESULTS_PATH.exists():
        raise SystemExit(
            f"{CITATION_RESULTS_PATH} not found -- run stage3_citation.run_citation_eval first."
        )

    rows = [
        json.loads(l)
        for l in CITATION_RESULTS_PATH.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    for row in rows:
        repo_id = row["question_id"].split("::")[0]
        repo_root = REPO_STORE_ROOT / repo_id
        citations = extract_citations(row["answer"])
        validated = [validate_citation(repo_root, c) for c in citations]
        row["citations"] = validated
        row["num_citations"] = len(validated)
        row["any_valid_file"] = any(c["file_valid"] for c in validated)
        row["any_valid_line"] = any(c.get("line_valid") for c in validated)

    with CITATION_RESULTS_PATH.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    _print_summary(rows)
    print(f"\nRe-written {CITATION_RESULTS_PATH} (no new /chat calls, no new cost)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-questions", type=int, default=25)
    parser.add_argument(
        "--pilot",
        type=int,
        default=None,
        help="Override --num-questions for a quick cost check",
    )
    parser.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Re-extract/re-validate citations from existing results, no new /chat calls",
    )
    args = parser.parse_args()
    if args.recompute:
        recompute_from_existing()
    else:
        asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
