#!/usr/bin/env python3
"""
5.3 RAG 检索延迟基准测试脚本

扫描向量索引根目录下的所有仓库，对每个仓库运行固定查询，分别测量：
  - BM25 稀疏检索耗时（code / text 两类）
  - FAISS 稠密检索耗时（code / text 两类）
  - 完整 RAG 问答（含 LLM 生成）耗时，HyDE 开/关对比

将所有仓库的结果汇总到一个 CSV，并生成分析图表（PNG）。

用法：
    cd docker
    PYTHONPATH=. python ../test/chapter5/03_benchmark_rag_latency.py \
        --vector-store-dir ../data/vector_store \
        [--questions "How does the router work?" "What is the main entry point?"] \
        [--rounds 3] \
        [--output results.csv] \
        [--chart-dir ../test/chapter5/charts]
"""
import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# 辅助：发现有效的向量数据库目录
# ---------------------------------------------------------------------------

def _discover_repos(vector_store_dir: str) -> List[Path]:
    """
    扫描 vector_store_dir 下的所有一级子目录，
    返回包含 code/ 或 text/ 子目录（带 index.faiss）的有效仓库路径列表。
    """
    root = Path(vector_store_dir)
    if not root.exists():
        print(f"[ERROR] 向量索引根目录不存在: {root}")
        return []

    found = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        has_valid_category = any(
            (sub / cat / "index.faiss").exists() for cat in ("code", "text")
        )
        if has_valid_category:
            found.append(sub)

    return found


# ---------------------------------------------------------------------------
# 单仓库 × 单问题的基准测量
# ---------------------------------------------------------------------------

def _benchmark_retrieval_stages(vector_store_path: str, question: str) -> Dict:
    """测量各检索阶段耗时（BM25 + FAISS，code / text 两类）"""
    from src.ingestion.kb_loader import load_knowledge_base
    from src.core.retrieval import SparseBM25Index
    from src.core.chat import _extract_store_documents, _resolve_category_path, _resolve_vector_store_root

    root_path = _resolve_vector_store_root(vector_store_path)
    results = {}

    for category in ["code", "text"]:
        try:
            category_path = _resolve_category_path(root_path, category)
        except FileNotFoundError:
            continue

        # FAISS 加载（计时）
        t0 = time.perf_counter()
        dense_store = load_knowledge_base(category_path)
        results[f"{category}_load_s"] = round(time.perf_counter() - t0, 4)

        # 提取文档用于 BM25
        docs = _extract_store_documents(dense_store)

        # BM25 稀疏检索（含索引构建）
        if docs:
            t0 = time.perf_counter()
            bm25 = SparseBM25Index.build(docs)
            bm25.search(question, top_k=20)
            results[f"{category}_bm25_s"] = round(time.perf_counter() - t0, 4)

        # FAISS 稠密检索
        t0 = time.perf_counter()
        dense_store.similarity_search_with_score(question, k=20)
        results[f"{category}_dense_s"] = round(time.perf_counter() - t0, 4)

    return results


def _benchmark_full_chat(vector_store_path: str, question: str, hyde_enabled: bool) -> Dict:
    """测量完整 RAG 问答（含 LLM 生成）耗时"""
    suffix = "_hyde_on" if hyde_enabled else "_hyde_off"
    key = f"full_chat{suffix}_s"

    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return {key: "N/A (no API key)"}

    try:
        from src.core.chat import answer_question
    except ImportError:
        return {key: "N/A (import error)"}

    try:
        t0 = time.perf_counter()
        answer_question(
            vector_store_path,
            question,
            conversation_history=[],
            use_hyde=hyde_enabled,
        )
        return {key: round(time.perf_counter() - t0, 4)}
    except Exception as e:
        return {key: f"ERROR: {e}"}


# ---------------------------------------------------------------------------
# 单仓库基准入口
# ---------------------------------------------------------------------------

def _benchmark_repo(
    repo_path: Path,
    questions: List[str],
    rounds: int,
) -> List[Dict]:
    """
    对一个仓库运行所有问题的基准测试，返回行列表（每行 = 一个问题的平均结果）。
    """
    repo_name = repo_path.name
    rows = []

    for q in questions:
        print(f"\n  Query: {q[:70]}")

        # --- 检索阶段（多轮取均值）---
        retrieval_accum: Dict[str, List[float]] = {}
        for _ in range(rounds):
            try:
                stage = _benchmark_retrieval_stages(str(repo_path), q)
            except Exception as e:
                print(f"    [WARN] 检索基准失败: {e}")
                break
            for k, v in stage.items():
                if isinstance(v, (int, float)):
                    retrieval_accum.setdefault(k, []).append(v)

        retrieval_avg = {
            k: round(sum(v) / len(v), 4) for k, v in retrieval_accum.items()
        }
        for k, v in retrieval_avg.items():
            print(f"    {k}: {v}s  (avg/{rounds})")

        # --- 完整问答（各运行 1 次，LLM 调用成本高）---
        chat_on = _benchmark_full_chat(str(repo_path), q, hyde_enabled=True)
        chat_off = _benchmark_full_chat(str(repo_path), q, hyde_enabled=False)
        print(f"    full_chat_hyde_on : {list(chat_on.values())[0]}")
        print(f"    full_chat_hyde_off: {list(chat_off.values())[0]}")

        rows.append({"repo": repo_name, "question": q, **retrieval_avg, **chat_on, **chat_off})

    return rows


# ---------------------------------------------------------------------------
# 图表生成
# ---------------------------------------------------------------------------

RETRIEVAL_METRICS = [
    ("code_bm25_s",  "Code BM25"),
    ("code_dense_s", "Code FAISS Dense"),
    ("text_bm25_s",  "Text BM25"),
    ("text_dense_s", "Text FAISS Dense"),
]
CHAT_METRICS = [
    ("full_chat_hyde_on_s",  "Full RAG (HyDE ON)"),
    ("full_chat_hyde_off_s", "Full RAG (HyDE OFF)"),
]


def _numeric(val) -> Optional[float]:
    """将表格值转换为 float，失败返回 None。"""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _repo_averages(all_rows: List[Dict]) -> Dict[str, Dict[str, float]]:
    """
    将各行按 repo 分组，对每个指标取均值（跨所有问题）。
    返回 {repo_name: {metric: avg_value}}。
    """
    accum: Dict[str, Dict[str, List[float]]] = {}
    all_metrics = [m for m, _ in RETRIEVAL_METRICS + CHAT_METRICS]
    for row in all_rows:
        repo = row["repo"]
        accum.setdefault(repo, {m: [] for m in all_metrics})
        for m in all_metrics:
            v = _numeric(row.get(m))
            if v is not None:
                accum[repo][m].append(v)

    return {
        repo: {
            m: round(sum(vals) / len(vals), 4) if vals else None
            for m, vals in metrics.items()
        }
        for repo, metrics in accum.items()
    }


def _generate_charts(all_rows: List[Dict], chart_dir: Path) -> None:
    """生成两张分析图表并保存到 chart_dir。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("[WARN] matplotlib / numpy 未安装，跳过图表生成。")
        return

    chart_dir.mkdir(parents=True, exist_ok=True)
    repo_avgs = _repo_averages(all_rows)
    repos = list(repo_avgs.keys())

    # ------------------------------------------------------------------ #
    # 图 1：各仓库检索阶段延迟对比（分组柱状图）
    # ------------------------------------------------------------------ #
    metrics_ret = RETRIEVAL_METRICS
    n_repos = len(repos)
    n_metrics = len(metrics_ret)
    x = np.arange(n_repos)
    bar_width = 0.18
    offsets = np.linspace(-(n_metrics - 1) / 2, (n_metrics - 1) / 2, n_metrics) * bar_width

    fig, ax = plt.subplots(figsize=(max(8, n_repos * 2), 5))
    for i, (metric_key, label) in enumerate(metrics_ret):
        values = [repo_avgs[r].get(metric_key) for r in repos]
        bar_values = [v if v is not None else 0.0 for v in values]
        hatches = ["//" if v is None else "" for v in values]
        bars = ax.bar(x + offsets[i], bar_values, bar_width, label=label, zorder=3)
        for bar, hatch in zip(bars, hatches):
            bar.set_hatch(hatch)

    ax.set_xticks(x)
    ax.set_xticklabels(repos, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("Latency (s)")
    ax.set_title("RAG Retrieval Latency by Repository\n(avg across all test questions)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    fig.tight_layout()
    out1 = chart_dir / "retrieval_latency.png"
    fig.savefig(out1, dpi=150)
    plt.close(fig)
    print(f"  图表已保存: {out1}")

    # ------------------------------------------------------------------ #
    # 图 2：HyDE ON vs OFF 完整问答延迟对比
    # ------------------------------------------------------------------ #
    metrics_chat = CHAT_METRICS
    n_chat = len(metrics_chat)
    offsets_chat = np.linspace(-(n_chat - 1) / 2, (n_chat - 1) / 2, n_chat) * bar_width

    fig2, ax2 = plt.subplots(figsize=(max(8, n_repos * 2), 5))
    colors = ["#4C72B0", "#DD8452"]
    for i, (metric_key, label) in enumerate(metrics_chat):
        values = [repo_avgs[r].get(metric_key) for r in repos]
        bar_values = [v if v is not None else 0.0 for v in values]
        hatches = ["//" if v is None else "" for v in values]
        bars = ax2.bar(x + offsets_chat[i], bar_values, bar_width,
                       label=label, color=colors[i], zorder=3)
        for bar, hatch in zip(bars, hatches):
            bar.set_hatch(hatch)
        for bar, val, orig in zip(bars, bar_values, values):
            if orig is not None:
                ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                         f"{val:.2f}s", ha="center", va="bottom", fontsize=7)

    ax2.set_xticks(x)
    ax2.set_xticklabels(repos, rotation=20, ha="right", fontsize=9)
    ax2.set_ylabel("Latency (s)")
    ax2.set_title("Full RAG Answer Latency: HyDE ON vs OFF\n(avg across all test questions)")
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    fig2.tight_layout()
    out2 = chart_dir / "full_chat_latency_hyde.png"
    fig2.savefig(out2, dpi=150)
    plt.close(fig2)
    print(f"  图表已保存: {out2}")

    # ------------------------------------------------------------------ #
    # 图 3：各仓库各阶段延迟堆叠总览（横向堆叠条形图）
    # ------------------------------------------------------------------ #
    stack_metrics = [
        ("code_load_s",  "Code Load"),
        ("code_bm25_s",  "Code BM25"),
        ("code_dense_s", "Code Dense"),
        ("text_load_s",  "Text Load"),
        ("text_bm25_s",  "Text BM25"),
        ("text_dense_s", "Text Dense"),
    ]
    fig3, ax3 = plt.subplots(figsize=(10, max(4, n_repos * 0.9)))
    y_pos = np.arange(n_repos)
    left = np.zeros(n_repos)
    cmap = plt.get_cmap("tab10")
    for idx, (sk, slabel) in enumerate(stack_metrics):
        vals = [repo_avgs[r].get(sk) or 0.0 for r in repos]
        ax3.barh(y_pos, vals, left=left, label=slabel, color=cmap(idx), zorder=3)
        left += np.array(vals)

    ax3.set_yticks(y_pos)
    ax3.set_yticklabels(repos, fontsize=9)
    ax3.set_xlabel("Cumulative Latency (s)")
    ax3.set_title("Retrieval Stage Breakdown by Repository")
    ax3.legend(loc="lower right", fontsize=8)
    ax3.grid(axis="x", linestyle="--", alpha=0.5, zorder=0)
    fig3.tight_layout()
    out3 = chart_dir / "retrieval_breakdown.png"
    fig3.savefig(out3, dpi=150)
    plt.close(fig3)
    print(f"  图表已保存: {out3}")


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="多仓库 RAG 检索延迟基准测试")
    parser.add_argument(
        "--vector-store-dir",
        default="../data/vector_store",
        help="包含所有仓库向量索引的父目录（默认: ../data/vector_store）",
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        default=[
            "How does the main entry point work?",
            "What is the routing architecture?",
            "How are errors handled in the API?",
        ],
        help="测试查询列表",
    )
    parser.add_argument("--rounds", type=int, default=3, help="检索阶段每问题重复轮次（取均值）")
    parser.add_argument(
        "--output",
        type=str,
        default="../test/chapter5/rag_benchmark_results.csv",
        help="输出 CSV 文件路径",
    )
    parser.add_argument(
        "--chart-dir",
        type=str,
        default="../test/chapter5/charts",
        help="图表输出目录",
    )
    args = parser.parse_args()

    # 发现所有有效仓库
    repos = _discover_repos(args.vector_store_dir)
    if not repos:
        print("[ERROR] 未找到任何有效的向量数据库，请检查 --vector-store-dir。")
        sys.exit(1)

    print(f"[INFO] 发现 {len(repos)} 个向量数据库:")
    for r in repos:
        print(f"  - {r.name}")

    all_rows: List[Dict] = []

    for repo_path in repos:
        print(f"\n{'='*65}")
        print(f"Repo: {repo_path.name}")
        print(f"{'='*65}")
        rows = _benchmark_repo(repo_path, args.questions, args.rounds)
        all_rows.extend(rows)

    # 输出汇总 CSV
    if all_rows:
        # 收集全部字段名（保持插入顺序，兼容不同仓库字段不同的情况）
        fieldnames: List[str] = []
        seen = set()
        for row in all_rows:
            for k in row:
                if k not in seen:
                    fieldnames.append(k)
                    seen.add(k)

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="N/A")
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\n[CSV] 汇总结果已写入: {output_path}  ({len(all_rows)} 行)")

    # 生成图表
    print("\n[CHART] 生成分析图表...")
    _generate_charts(all_rows, Path(args.chart_dir))

    print("\n[完成] 基准测试结束。")


if __name__ == "__main__":
    main()
