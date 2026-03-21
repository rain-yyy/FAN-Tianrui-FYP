#!/usr/bin/env python3
"""
Lexical search benchmark for GrepSearchTool (no LLM).

Usage:
  cd docker && PYTHONPATH=. python eval/agentic_search/run_lexical_eval.py --repo-root .
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
import types
from pathlib import Path


def _load_grep_tool_only(docker_pkg: Path):
    """Load GrepSearchTool without importing src.agent (avoids optional graph deps)."""
    src_path = docker_pkg / "src"
    src = types.ModuleType("src")
    src.__path__ = [str(src_path)]
    sys.modules["src"] = src

    agent_pkg = types.ModuleType("src.agent")
    agent_pkg.__path__ = [str(src_path / "agent")]
    sys.modules["src.agent"] = agent_pkg

    state_path = src_path / "agent" / "state.py"
    spec_st = importlib.util.spec_from_file_location("src.agent.state", state_path)
    mod_st = importlib.util.module_from_spec(spec_st)
    sys.modules["src.agent.state"] = mod_st
    assert spec_st.loader
    spec_st.loader.exec_module(mod_st)

    tools_pkg = types.ModuleType("src.agent.tools")
    tools_pkg.__path__ = [str(src_path / "agent" / "tools")]
    sys.modules["src.agent.tools"] = tools_pkg

    grep_path = src_path / "agent" / "tools" / "grep_tool.py"
    spec_g = importlib.util.spec_from_file_location("src.agent.tools.grep_tool", grep_path)
    mod_g = importlib.util.module_from_spec(spec_g)
    sys.modules["src.agent.tools.grep_tool"] = mod_g
    assert spec_g.loader
    spec_g.loader.exec_module(mod_g)
    return mod_g.GrepSearchTool


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GrepSearchTool lexical benchmark")
    parser.add_argument(
        "--repo-root",
        type=str,
        default=".",
        help="Repository root to search (use docker/ or repo root)",
    )
    parser.add_argument(
        "--cases",
        type=str,
        default=None,
        help="Path to lexical_cases.json (default: alongside this script)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    script_dir = Path(__file__).resolve().parent
    cases_path = Path(args.cases) if args.cases else script_dir / "lexical_cases.json"

    if not repo_root.is_dir():
        print(json.dumps({"error": "repo_root not a directory", "path": str(repo_root)}))
        return 2

    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    # Resolve docker package root (parent of eval/)
    eval_file = Path(__file__).resolve()
    docker_pkg = eval_file.parents[2]
    if not (docker_pkg / "src" / "agent").is_dir():
        print(json.dumps({"error": "cannot find docker/src/agent", "docker_pkg": str(docker_pkg)}))
        return 2

    GrepSearchTool = _load_grep_tool_only(docker_pkg)
    tool = GrepSearchTool(str(repo_root))
    results = []
    wall_times = []

    for c in cases:
        cid = c["id"]
        t0 = time.perf_counter()
        piece = tool.execute(
            pattern=c["pattern"],
            is_regex=c.get("is_regex", False),
            file_pattern=c.get("file_pattern"),
            max_results=int(c.get("max_results", 50)),
            case_sensitive=bool(c.get("case_sensitive", False)),
            path_prefix=c.get("path_prefix"),
            context_lines=int(c.get("context_lines", 2)),
        )
        wall_ms = int((time.perf_counter() - t0) * 1000)
        md = dict(piece.metadata or {})
        sources = md.get("sources") or []
        num = int(md.get("num_matches") or 0)
        min_m = int(c.get("min_matches", 1))
        need = c.get("path_must_contain", "")
        hit = any(need in s for s in sources) if need else num >= min_m
        ok = hit and num >= min_m and piece.relevance_score > 0

        results.append({
            "id": cid,
            "ok": ok,
            "wall_ms": wall_ms,
            "relevance": piece.relevance_score,
            "metadata": {
                "engine": md.get("engine"),
                "num_matches": md.get("num_matches"),
                "files_searched": md.get("files_searched"),
                "truncated": md.get("truncated"),
                "used_python_fallback": md.get("used_python_fallback"),
                "error": md.get("error"),
            },
        })
        if ok:
            wall_times.append(wall_ms)

    passed = sum(1 for r in results if r["ok"])
    summary = {
        "repo_root": str(repo_root),
        "cases_total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "median_wall_ms": float(statistics.median(wall_times)) if wall_times else None,
        "cases": results,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
