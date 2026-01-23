# 项目重构方案 (Project Refactoring Plan)

为了提高项目的可维护性和清晰度，建议将项目结构进行模块化整理，并简化文件名。

## 1. 建议的目录结构 (Proposed Directory Structure)

我们将代码分为几个主要模块：



ha

-`src/`: 核心源代码

-`core/`: 核心 RAG 逻辑

-`ingest/`: 数据处理和入库

-`wiki/`: Wiki 生成相关逻辑

-`scripts/`: 可执行脚本 (入口文件)

-`config/`: 配置文件

-`docs/`: 文档

```text

FAN-Tianrui-FYP/

├── config/                     # 配置文件 (保持不变)

│   └── repo_config.json

├── docs/                       # 项目文档

│   └── roadmap.md              # 原 RAG优化路线.md

├── scripts/                    # 脚本/入口文件

│   ├── generate_wiki.py        # 原 项目结构生成.py

│   └── setup_repo.py           # 原 setup_repository.py

├── src/                        # 核心代码库

│   ├── __init__.py

│   ├── client.py               # 原 deepseek_client.py

│   ├── prompts.py              # 原 prompts/prompts.py (移动到源码目录)

│   ├── core/                   # RAG 核心逻辑

│   │   ├── __init__.py

│   │   ├── chat.py             # 原 RAG对话.py

│   │   └── retrieval.py        # 原 hybrid_retrieval.py

│   ├── ingest/                 # 数据处理与向量化

│   │   ├── __init__.py

│   │   ├── loader.py           # 原 knowledge_base_loader.py

│   │   ├── processor.py        # 原 file_processor.py

│   │   ├── splitter.py         # 原 document_splitter.py

│   │   └── vector_store.py     # 原 vector_store_creator.py

│   └── wiki/                   # Wiki 生成模块

│       ├── __init__.py

│       ├── content_gen.py      # 原 wiki_content_generator.py

│       └── struct_gen.py       # 原 structure_generator.py

├── RepoMapper/                 # 外部/独立工具库 (保持根目录或移入 libs/)

├── .gitignore

├── requirements.txt

└── README.md

```

---

## 2. 文件重命名对照表 (File Renaming Mapping)

| 原文件名 | 新文件名 (路径相对于根目录) | 功能描述 |

| :--- | :--- | :--- |

| **`RAG对话.py`** | `src/core/chat.py` | RAG 对话主逻辑，处理用户输入和检索增强生成。 |

| **`hybrid_retrieval.py`** | `src/core/retrieval.py` | 混合检索逻辑 (关键词 + 向量)。 |

| **`deepseek_client.py`** | `src/client.py` | DeepSeek API 客户端封装。 |

| **`knowledge_base_loader.py`** | `src/ingest/loader.py` | 负责加载知识库文档。 |

| **`file_processor.py`** | `src/ingest/processor.py` | 处理文件内容，提取信息。 |

| **`document_splitter.py`** | `src/ingest/splitter.py` | 将长文档切分为 chunks。 |

| **`vector_store_creator.py`** | `src/ingest/vector_store.py` | 创建和管理 FAISS 向量数据库。 |

| **`structure_generator.py`** | `src/wiki/struct_gen.py` | 生成项目 Wiki 的目录结构逻辑。 |

| **`wiki_content_generator.py`** | `src/wiki/content_gen.py` | 生成具体的 Wiki 页面内容。 |

| **`项目结构生成.py`** | `scripts/generate_wiki.py` | **脚本入口**：执行 Wiki 生成任务的主程序。 |

| **`setup_repository.py`** | `scripts/setup_repo.py` | **脚本入口**：初始化仓库配置。 |

| **`prompts/prompts.py`** | `src/prompts.py` | 存放 Prompt 模板。 |

| **`RAG优化路线.md`** | `docs/roadmap.md` | 项目规划和优化路线图。 |

---

## 3. 下一步操作建议

如果您同意这个方案，接下来的步骤将是：

1. 创建 `src`, `src/core`, `src/ingest`, `src/wiki`, `scripts`, `docs` 等文件夹。
2. 移动并重命名文件。

3.**关键步骤**：修改代码中的 `import` 引用。例如，`from hybrid_retrieval import ...` 需要改为 `from src.core.retrieval import ...`。

4. 清理空的旧文件夹 (如 `prompts/` 如果被清空)。
