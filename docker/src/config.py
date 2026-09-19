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

def get_wiki_content_concurrency(config: Dict[str, Any] | None = None) -> int:
    """获取 wiki 正文生成并发数，默认 3"""
    cfg = config or CONFIG
    return cfg.get("wiki_generation", {}).get("content_concurrency", 3)


def get_rag_retry_delays_sec(config: Dict[str, Any] | None = None) -> list:
    """RAG 索引失败后台重试前的等待秒数序列，默认 [30, 120, 300]"""
    cfg = config or CONFIG
    return cfg.get("wiki_generation", {}).get("rag_retry_delays_sec", [30, 120, 300])


def _get_debug_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = config or CONFIG
    raw = cfg.get("debug")
    return raw if isinstance(raw, dict) else {}


def should_save_wiki_structure_raw_responses(config: Dict[str, Any] | None = None) -> bool:
    """是否将 wiki 结构生成的原始 LLM 响应落盘到 ./wiki_structure_raw/，默认关闭（避免长期运行进程磁盘无限增长）。"""
    return bool(_get_debug_config(config).get("save_wiki_structure_raw_responses", False))


def should_save_rag_chunk_debug(config: Dict[str, Any] | None = None) -> bool:
    """是否将 RAG 索引的 chunk 切分结果落盘到向量库目录下的 chunk_debug/，默认关闭。"""
    return bool(_get_debug_config(config).get("save_rag_chunk_debug", False))


def get_ingestion_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = config or CONFIG
    raw = cfg.get("ingestion")
    return raw if isinstance(raw, dict) else {}
