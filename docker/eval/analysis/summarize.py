"""Aggregate eval results into resume-ready numbers.

Reads results/*.jsonl from all three stages and writes the exact numbers to
drop into the resume bullets as markdown to results/summary.md.

Usage (from `docker/eval/`):
    python -m analysis.summarize
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from common.config import (  # noqa: E402
    CITATION_RESULTS_PATH,
    GENERATION_RESULTS_PATH,
    RESULTS_DIR,
    RETRIEVAL_RESULTS_PATH,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def summarize_stage1() -> str:
    rows = [
        r
        for r in _read_jsonl(GENERATION_RESULTS_PATH)
        if r.get("status") == "completed"
    ]
    if not rows:
        return "## Stage 1 -- Wiki generation (time / LOC / cost)\n\nNo completed runs yet.\n"

    locs = [r["loc"] for r in rows if r.get("loc") is not None]
    times_min = [r["wall_clock_sec"] / 60 for r in rows]
    costs = [r["total_cost_usd"] for r in rows]

    lines = [
        "## Stage 1 -- Wiki generation (time / LOC / cost)",
        "",
        f"- Repos measured: **{len(rows)}**",
        f"- LOC range: {min(locs):,} -- {max(locs):,} (median {int(statistics.median(locs)):,})"
        if locs
        else "- LOC: n/a",
        f"- Time: median {statistics.median(times_min):.1f} min, max {max(times_min):.1f} min",
        f"- Cost per repo: mean ${statistics.mean(costs):.3f}, max ${max(costs):.3f}",
        "",
        "Suggested resume fill-in: "
        f'"generates wikis for repos up to {max(locs) // 1000 if locs else "?"}K LOC '
        f'in {round(statistics.median(times_min))} min at ~${statistics.mean(costs):.2f} per repo"',
        "",
    ]
    return "\n".join(lines)


def summarize_stage2() -> str:
    rows = _read_jsonl(RETRIEVAL_RESULTS_PATH)
    if not rows:
        return "## Stage 2 -- Retrieval Recall@5\n\nNo results yet.\n"

    n = len(rows)
    repos = {r["repo_id"] for r in rows}
    configs = ("dense_only", "hybrid_no_hyde_mmr", "full_production")
    pct = {
        c: 100.0 * sum(1 for r in rows if r["hit_by_config"][c]) / n for c in configs
    }

    lines = [
        "## Stage 2 -- Retrieval Recall@5",
        "",
        f"- N = {n} questions over M = {len(repos)} repos",
        f"- dense-only:          {pct['dense_only']:.1f}%",
        f"- hybrid (no HyDE/MMR): {pct['hybrid_no_hyde_mmr']:.1f}%",
        f"- full production (hybrid+HyDE+MMR): {pct['full_production']:.1f}%",
        "",
        "Suggested resume fill-in: "
        f'"improving file-level Recall@5 from {pct["dense_only"]:.0f}% (dense-only) to '
        f'{pct["full_production"]:.0f}% on a {n}-question benchmark over {len(repos)} repos"',
        "",
    ]
    return "\n".join(lines)


def summarize_stage3() -> str:
    rows = _read_jsonl(CITATION_RESULTS_PATH)
    if not rows:
        return "## Stage 3 -- Citation validity\n\nNo results yet.\n"

    citations = [c for r in rows for c in r["citations"]]
    precise = [c for c in citations if c.get("kind") == "line_range"]
    n_answers = len(rows)
    n_with_citation = sum(1 for r in rows if r["num_citations"] > 0)
    file_valid_pct = (
        100.0 * sum(1 for c in citations if c["file_valid"]) / len(citations)
        if citations
        else 0.0
    )
    line_checked = [c for c in precise if c.get("line_valid") is not None]
    line_valid_pct = (
        100.0 * sum(1 for c in line_checked if c["line_valid"]) / len(line_checked)
        if line_checked
        else None
    )

    lines = [
        "## Stage 3 -- Citation validity",
        "",
        f"- Answers: {n_answers} ({n_with_citation} contained >=1 citation of any kind)",
        f"- Citations extracted: {len(citations)} ({len(precise)} with an explicit `file:line` range, "
        f"{len(citations) - len(precise)} file-only)",
        f"- File-valid (any kind -- does the cited file exist): {file_valid_pct:.1f}%",
        (
            f"- File+line-valid (of the {len(precise)} `file:line`-range citations): {line_valid_pct:.1f}%"
            if line_valid_pct is not None
            else "- File+line-valid: n/a"
        ),
        "",
        "Suggested resume fill-in: "
        f'"{file_valid_pct:.0f}% of which resolve to valid code locations"',
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    report = "\n".join(
        [
            "# Eval results summary",
            "",
            summarize_stage1(),
            summarize_stage2(),
            summarize_stage3(),
        ]
    )
    out_path = RESULTS_DIR / "summary.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
