import os
import json
import logging
from pathlib import Path
from typing import Union, Dict, Any

from src.paths import PROJECT_ROOT

# 使用通用日志记录器
logger = logging.getLogger("app.config")

# 默认配置文件路径 (环境变量优先)
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", str(PROJECT_ROOT / "config" / "repo_config.json")))

def load_config(config_path: Union[str, Path] = CONFIG_PATH) -> Dict[str, Any]:
    """
    从指定路径加载 JSON 配置文件。
    """
    if isinstance(config_path, str):
        config_path = Path(config_path)
        
    if not config_path.exists():
        # 备选路径：相对于当前工作目录查找
        alt_path = Path("config/repo_config.json").absolute()
        if alt_path.exists():
            config_path = alt_path
        else:
            logger.error(f"Configuration file not found at: {config_path}")
            return {}
            
    logger.info(f"Loading configuration from: {config_path}")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading config file: {e}")
        return {}

# 预加载全局配置供其他模块直接使用
CONFIG = load_config()

def _config_value(section: str, key: str, default: Any, config: Dict[str, Any] | None = None) -> Any:
    """
    从 CONFIG[section][key] 读取一个值，section 缺失/类型不对时回退默认值。
    所有下面的 get_X()/should_X() 访问器都建立在这一个实现上，避免每个访问器各自
    重复 `cfg.get(section, {}).get(key, default)`，把 key 名和默认值散落在各处。
    """
    cfg = config or CONFIG
    section_dict = cfg.get(section)
    if not isinstance(section_dict, dict):
        return default
    return section_dict.get(key, default)


def get_wiki_content_concurrency(config: Dict[str, Any] | None = None) -> int:
    """获取 wiki 正文生成并发数，默认 3"""
    return _config_value("wiki_generation", "content_concurrency", 3, config)


def get_rag_retry_delays_sec(config: Dict[str, Any] | None = None) -> list:
    """RAG 索引失败后台重试前的等待秒数序列，默认 [30, 120, 300]"""
    return _config_value("wiki_generation", "rag_retry_delays_sec", [30, 120, 300], config)


def should_save_wiki_structure_raw_responses(config: Dict[str, Any] | None = None) -> bool:
    """是否将 wiki 结构生成的原始 LLM 响应落盘到 ./wiki_structure_raw/，默认关闭（避免长期运行进程磁盘无限增长）。"""
    return bool(_config_value("debug", "save_wiki_structure_raw_responses", False, config))


def should_save_rag_chunk_debug(config: Dict[str, Any] | None = None) -> bool:
    """是否将 RAG 索引的 chunk 切分结果落盘到向量库目录下的 chunk_debug/，默认关闭。"""
    return bool(_config_value("debug", "save_rag_chunk_debug", False, config))


# ── ingestion 段：分块/批量大小等调优常量，每个访问器自己拥有 key 名和默认值 ──

def get_embedding_inner_batch_size(config: Dict[str, Any] | None = None) -> int:
    """每次实际发送给 OpenRouter Embeddings API 的最大文本条数，默认 20"""
    return _config_value("ingestion", "embedding_inner_batch_size", 20, config)


def get_embedding_inner_batch_sleep_sec(config: Dict[str, Any] | None = None) -> float:
    """两次 Embeddings API 请求之间的冷却时间（秒），默认 1.0"""
    return _config_value("ingestion", "embedding_inner_batch_sleep_sec", 1.0, config)


def get_vector_store_batch_size(config: Dict[str, Any] | None = None) -> int:
    """每批送入 Qdrant 的文档数，默认 50"""
    return _config_value("ingestion", "vector_store_batch_size", 50, config)


def get_vector_store_inter_batch_sleep_sec(config: Dict[str, Any] | None = None) -> float:
    """两次 Qdrant 外层批次之间的等待时间（秒），默认 2.0"""
    return _config_value("ingestion", "vector_store_inter_batch_sleep_sec", 2.0, config)


def get_chunk_max_size(config: Dict[str, Any] | None = None) -> int:
    """SemanticDocumentSplitter 单个 chunk 的最大字符数，默认 2000"""
    return _config_value("ingestion", "chunk_max_size", 2000, config)


def get_chunk_overlap_size(config: Dict[str, Any] | None = None) -> int:
    """SemanticDocumentSplitter 相邻 chunk 的重叠字符数，默认 150"""
    return _config_value("ingestion", "chunk_overlap_size", 150, config)


def get_code_chunk_min_size(config: Dict[str, Any] | None = None) -> int:
    """低于此字符数的代码 chunk 会与同文件相邻块合并，默认 150"""
    return _config_value("ingestion", "code_chunk_min_size", 150, config)


def get_code_chunk_max_size(config: Dict[str, Any] | None = None) -> int:
    """超过此字符数的代码 chunk 会按行分割，默认 2000"""
    return _config_value("ingestion", "code_chunk_max_size", 2000, config)


# ── chat 段：Agent 循环/记忆/检索调优常量。此前分散为 core/chat.py 与
# agent/graph.py 里的 Python 字面量（CATEGORY_TOP_K、HYBRID_*_WEIGHT、
# max_iterations=5 等），无法通过 repo_config.json 调整；现统一收进这里 ──

def get_chat_max_tool_iterations(config: Dict[str, Any] | None = None) -> int:
    """单轮对话内允许的最大工具调用轮次，默认 6"""
    return _config_value("chat", "max_tool_iterations", 6, config)


def get_chat_context_token_budget(config: Dict[str, Any] | None = None) -> int:
    """喂给聊天模型的历史+系统提示词总 token 预算（近似估算），默认 12000"""
    return _config_value("chat", "context_token_budget", 12000, config)


def get_chat_reserved_output_tokens(config: Dict[str, Any] | None = None) -> int:
    """从 context_token_budget 中为模型输出预留的 token 数，默认 2000"""
    return _config_value("chat", "reserved_output_tokens", 2000, config)


def get_chat_history_summary_trigger_messages(config: Dict[str, Any] | None = None) -> int:
    """未摘要的消息数超过此值时后台触发一次会话摘要，默认 20"""
    return _config_value("chat", "history_summary_trigger_messages", 20, config)


def get_hybrid_dense_weight(config: dict[str, Any] | None = None) -> float:
    """混合检索中 dense 分数的权重，默认 0.6"""
    return _config_value("chat", "hybrid_dense_weight", 0.6, config)


def get_hybrid_sparse_weight(config: Dict[str, Any] | None = None) -> float:
    """混合检索中 sparse(BM25) 分数的权重，默认 0.4"""
    return _config_value("chat", "hybrid_sparse_weight", 0.4, config)


def get_mmr_lambda(config: Dict[str, Any] | None = None) -> float:
    """MMR 多样性选择的 lambda（1=纯相关性，0=纯多样性），默认 0.5"""
    return _config_value("chat", "mmr_lambda", 0.5, config)


def get_retrieval_k_multipliers(config: Dict[str, Any] | None = None) -> tuple:
    """返回 (dense_k_multiplier, sparse_k_multiplier, max_method_k)，默认 (4, 3, 30)"""
    cfg = config or CONFIG
    return (
        _config_value("chat", "dense_k_multiplier", 4, cfg),
        _config_value("chat", "sparse_k_multiplier", 3, cfg),
        _config_value("chat", "max_method_k", 30, cfg),
    )


def get_max_total_candidates(config: Dict[str, Any] | None = None) -> int:
    """跨分类融合后保留的最大候选数，默认 50"""
    return _config_value("chat", "max_total_candidates", 50, config)


def get_category_top_k(config: Dict[str, Any] | None = None) -> Dict[str, int]:
    """rag_search 每个分类的基础 top_k，默认 {"code": 20, "text": 20}"""
    return _config_value("chat", "category_top_k", {"code": 20, "text": 20}, config)


def should_use_hyde(config: Dict[str, Any] | None = None) -> bool:
    """是否对 'text' 分类的 dense 查询启用 HyDE 增强，默认开启"""
    return bool(_config_value("chat", "hyde_enabled", True, config))


def get_web_search_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """整段透传 web_search 配置（provider/api key/allowed_domains/timeout/max_results）"""
    cfg = config or CONFIG
    section = cfg.get("web_search")
    return section if isinstance(section, dict) else {}
