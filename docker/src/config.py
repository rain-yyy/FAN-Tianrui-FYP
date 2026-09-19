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
