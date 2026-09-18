"""
AI 客户端模块 — 基于 LangChain ChatOpenRouter。

使用方式:
    from src.clients import get_llm, ChatOpenRouter

    llm = get_llm("rag_answer", temperature=0.1)
    result = llm.invoke(messages).content
"""

import logging
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_openrouter import ChatOpenRouter

load_dotenv()

logger = logging.getLogger("app.clients")

_DEFAULT_MODEL = "google/gemini-2.5-flash"

__all__ = ["ChatOpenRouter", "StrOutputParser", "get_llm", "get_model_name"]


def get_model_name(config: Dict[str, Any] | None = None, model_key: str = "rag_answer") -> str:
    """
    从配置中获取指定用途的模型名称。

    Args:
        config: 配置字典（None 时使用全局 CONFIG）
        model_key: ai_models.models 下的键名（如 "rag_answer"、"wiki_structure"）

    Returns:
        OpenRouter 模型 slug，例如 "google/gemini-2.5-flash"
    """
    from src.config import CONFIG

    cfg = config or CONFIG
    model_name = cfg.get("ai_models", {}).get("models", {}).get(model_key)
    if not model_name:
        logger.warning(
            "Model key '%s' not found in config, falling back to default: %s",
            model_key,
            _DEFAULT_MODEL,
        )
        model_name = _DEFAULT_MODEL
    return model_name


def get_llm(
    model_key: str,
    config: Dict[str, Any] | None = None,
    **kwargs: Any,
) -> ChatOpenRouter:
    """
    根据配置键名返回配置好的 ChatOpenRouter 实例。

    Args:
        model_key: 配置文件中 ai_models.models 下的键名
        config:    配置字典（None 时使用全局 CONFIG）
        **kwargs:  透传给 ChatOpenRouter 的参数（如 temperature、max_tokens）

    Returns:
        ChatOpenRouter 实例，可直接调用 .invoke() / .stream()

    Example:
        llm = get_llm("rag_answer", temperature=0.1)
        answer = llm.invoke(messages).content

        llm = get_llm("wiki_content", max_tokens=1800)
        for chunk in llm.stream(messages):
            print(chunk.content, end="")
    """
    model_name = get_model_name(config, model_key)
    logger.debug("Creating ChatOpenRouter for key='%s' model='%s'", model_key, model_name)
    return ChatOpenRouter(model=model_name, **kwargs)
