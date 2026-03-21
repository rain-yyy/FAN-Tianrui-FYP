# Agentic search / `grep_search` 评测

## 内容

- **`lexical_cases.json`**：面向 `GrepSearchTool` 的词法检索用例（不调用 LLM，可 CI 跑）。
- **`run_lexical_eval.py`**：执行用例、汇总 **命中率、延迟、引擎（ripgrep / python）**。

## 运行

在仓库根目录：

```bash
cd docker
PYTHONPATH=. python eval/agentic_search/run_lexical_eval.py --repo-root .
```

或指定本仓库根（含 `frontend/`、`docker/` 等）：

```bash
cd docker
PYTHONPATH=. python eval/agentic_search/run_lexical_eval.py --repo-root ../..
```

## 指标说明

脚本输出 JSON，含：

- `passed` / `failed` 数  
- `cases[]`：每题的 `ok`、`wall_ms`、`metadata`（`engine`、`num_matches`、`files_searched`、`truncated`）  
- `median_wall_ms`：所有成功用例的墙钟耗时中位数（用于对比升级前后）

## 与完整 Agent 评测的关系

完整 **工具路由准确率**（Planner/Router 是否首选 `grep_search`）依赖 LLM，需单独题集 + `run_agent` 或 HTTP；本目录提供 **可回归的 lexical 工具层基线**。
