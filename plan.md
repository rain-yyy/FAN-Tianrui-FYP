---
name: Ch5-6 报告与测试
overview: 在仓库根目录新增 `test/`（benchmark/检查表/README），在 `docker/tests/` 补充针对 `ts_parser.py` 与流水线闭环的 PyTest；另新建一份完整第五～六章报告稿 Markdown，在大纲基础上结合代码与可查文献补全 5.1、5.5 与第六章全文，并允许调整与原大纲不一致处。
todos:
  - id: add-test-folder
    content: 创建 test/ 目录：README、5.2 对照说明、5.3 计时脚本与表格模板、5.4 定性 rubric
    status: pending
  - id: pytest-ts-parser
    content: 新增 docker/tests/test_ts_parser.py（解析断言 + 可选 skip）
    status: pending
  - id: pytest-pipeline-mock
    content: 新增 docker/tests/test_wiki_pipeline_integration.py（mock 闭环）
    status: pending
  - id: dev-deps-doc
    content: 补充 pytest 安装说明（requirements-dev 或 test/README）
    status: pending
  - id: report-md-full
    content: 新建完整第五～六章 report Markdown（5.1、5.5、第6章 + 修订 5.2–5.4 与实现对应）
    status: pending
isProject: false
---

# 第五章测试脚本与报告补全计划

## 背景对齐

- 大纲来源：[report.md](report.md)（5.1–5.5、6.1–6.4）。
- **5.2 单元测试**：大纲中的 `content_gen.py` 文件名冲突与并发已由 [docker/tests/test_content_gen.py](docker/tests/test_content_gen.py) 覆盖（`_safe_filename`、`_build_filename_map`、`generate` 并发等）；需在报告中明确引用现有用例，并**新增** `ts_parser.py` 的解析准确性测试。
- **5.2 集成测试**：完整流水线在 [docker/src/core/wiki_pipeline.py](docker/src/core/wiki_pipeline.py)（克隆→结构→内容→向量索引等）。真实跑通依赖 Git/LLM/Supabase/R2，适合用 **fixture 临时目录 + 大面积 mock**（`unittest.mock.patch` 掉 `setup_repository`、`WikiContentGenerator.generate`、`create_and_save_vector_store`、`upload_wiki_to_r2` 等）验证**调用顺序与产物路径**，而非在 CI 中默认联网。
- **5.3 量化**：代码侧可编写**可重复测量脚本**（检索耗时、整轮 chat 耗时）；论文表格中的「14–25 分钟、30–50 页、~20s」保留为**实验记录/IR II 数据**，与脚本输出对照写入报告。
- **5.4 定性**：以**结构化检查表 + 可选自动化辅助**（从 Wiki JSON 抽取 `mermaid` 代码块、括号/关键字粗检）为主；Leiden 模块划分、图表是否与代码一致需**人工对照**，脚本只辅助不替代判断。
- **依赖**：`pytest` 已在测试文件中使用但未出现在 [docker/requirements.txt](docker/requirements.txt)；实施时在 `docker/requirements-dev.txt` 或文档中注明 `pip install pytest`（优先最小改动）。

## 1. 仓库根目录 `test/` 结构（你已选择根目录方案）

建议布局：

```text
test/
  README.md                      # 如何运行：cd docker && PYTHONPATH=. pytest …；如何运行 benchmark
  chapter5/
    02_functional_testing.md     # 5.2：用例编号 ↔ pytest 类/函数对应表（中文）
    03_benchmark_rag_latency.py  # 5.3：RAG 阶段计时（需本地向量库/API key 时在说明中标注）
    03_wiki_pipeline_timings.md  # 5.3：手工/脚本记录 wiki 各阶段耗时的表格模板与填写说明
    04_qualitative_rubric.md     # 5.4：模块划分、Mermaid 准确性、失败样例记录表
```

- `03_benchmark_rag_latency.py`：从 `docker` 包导入 [docker/src/core/chat.py](docker/src/core/chat.py) / [docker/src/core/retrieval.py](docker/src/core/retrieval.py)，对固定 `question` + 已有 `vector_store` 路径（通过参数传入）循环测量：可选 HyDE 开关、`_gather_hybrid_candidates` 或公开封装函数耗时 vs 完整 `chat` 路径；输出 CSV 或控制台表格供报告 5.3 粘贴。
- 若缺少现成向量库，脚本应 **graceful exit** 并提示「仅在有索引目录时运行」，避免误报失败。

## 2. `docker/tests/` 新增/调整（5.2 自动化）

| 文件 | 内容要点 |
|------|----------|
| `test_ts_parser.py` | 使用 `TreeSitterParser` 对**内嵌 TS/TSX/Python 片段**断言：`parse_code` 返回非空 `CodeChunk`；对已知函数/类名检查 `name` 与 `node_type`；空扩展名返回 `[]`；极简文件走 `file` 整块回退逻辑（见 [docker/src/ingestion/ts_parser.py](docker/src/ingestion/ts_parser.py) 第 98–105 行）。**用 `pytest.importorskip` 或 try/except** 处理未安装 tree-sitter 语言包时的 skip，避免裸机无依赖即红。 |
| `test_wiki_pipeline_integration.py` | `patch` `setup_repository` 返回 `tmp_path` 下假仓库；`patch` 结构生成/内容生成/RAG/上传为轻量返回值；调用 `wiki_pipeline` 中exported 的高层函数（如 `execute_generation_task` 或其子步骤，以只读代码后选**最少公共入口**为准），断言进度回调或关键文件是否写入。 |

集成测试范围以**证明闭环路径被串联**为目标，不重复测试 LLM 质量。

## 3. 新报告 Markdown 文件（补全大纲其余部分）

- **新文件**（名称建议：`docs/report_chapter5_6_full.md` 或你指定的根路径文件名）：写入完整可读稿，替换/调整 [report.md](report.md) 中不合适表述。
- **5.1 Testing Environment and Dataset**：结合 [explain.md](explain.md) 与部署常识撰写——Fly.io 机器规格写「以实际 `fly.toml`/仪表盘为准并列表」；Vercel 写前端构建与边缘无关 RAG；测试仓库 **jsoncrack / react-resume / openclaw** 与 [docker/config/repo_config.json](docker/config/repo_config.json) 若存在则交叉引用。官方文档链接（Fly、Vercel）在实施阶段用网络检索补全 URL。
- **5.5 Comparison of Retrieval Strategies**：依据 [docker/src/core/retrieval.py](docker/src/core/retrieval.py) 中 `hybrid_retrieve`、BM25、`CommunityFirstRetriever`（与主路径接入情况对照 [docker/src/core/chat.py](docker/src/core/chat.py)）撰写；说明「混合检索利于符号/配置项」的机制（稀疏项匹配）；诚实写 **CommunityFirst** 若未接入主 RAG 路径则与大纲 [17][18] 一致标为技术债。
- **第六章**：6.1 对照项目 Objectives（需回读开题/第一章若仓库内有）；6.2 写入 `valid_file_list`（[docker/src/wiki/struct_gen.py](docker/src/wiki/struct_gen.py)、[docker/src/prompts.py](docker/src/prompts.py)）、AST/内存、`ThreadPoolExecutor`（[docker/src/wiki/content_gen.py](docker/src/wiki/content_gen.py)）、任务目录隔离（`wiki_pipeline` 中 `_task_output_dir`）；6.3 OpenRouter 多模型；6.4 增量更新与 Mermaid 校验自动化——用语客观、可答辩。
- **文献 [1]–[20]**：在大纲中多为占位；成稿中用「待你最终核对」或与你 IR II PDF 一致的简短引用格式列出，避免捏造篇名。

## 4. 实施顺序建议

1. 新增 `test/` 下 README + chapter5 文档与 benchmark 脚本骨架。  
2. 新增 `docker/tests/test_ts_parser.py` 与集成测试用例。  
3. 验证：`cd docker && PYTHONPATH=. pytest tests/ -q`（处理 skip）。  
4. 撰写 `report_chapter5_6_full.md`，把 5.2–5.4 与 `test/`、`docker/tests/` 的对应关系写进正文。  

## 5. 风险与边界

- Tree-sitter 语言包在 CI/本地不一致 → 测试以 **skip + 文档说明** 为主。  
- 全链路集成无 mock 时过长且不稳定 → **禁止**将重流水线作为默认 CI 必跑，除非后续加 `pytest -m "integration"` 与可选网络。  
- 5.3 数字强依赖你 IR II 实测；脚本提供**方法**，表格数值以你实验为准。
**