"""
聊天会话标题 / 预览文本生成。
"""
import asyncio

from langchain_core.prompts import ChatPromptTemplate

from src.clients import get_llm, StrOutputParser
from src.utils.logger import setup_logger

logger = setup_logger("chat_titles")


def generate_chat_preview_sync(question: str) -> str:
    """
    Generate a short preview text (title) using simple truncation rules.
    This is fast and non-blocking - no LLM call.
    """
    question = question.strip()

    # Remove common prefixes
    prefixes_to_remove = [
        "[Current page context:",
        "User question:",
        "Question:",
    ]
    for prefix in prefixes_to_remove:
        if question.startswith(prefix):
            question = question[len(prefix):].strip()

    # Extract first meaningful sentence or phrase
    for delimiter in ["？", "?", "。", "\n", "，", ","]:
        if delimiter in question:
            question = question.split(delimiter)[0].strip()
            break

    # Truncate to reasonable length
    max_len = 40
    if len(question) <= max_len:
        return question if question else "New chat"

    # Try to cut at word boundary
    truncated = question[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]

    return truncated.strip() + "..." if truncated else "New chat"


async def generate_chat_preview_async(question: str) -> str:
    """
    Async wrapper for generating chat preview using LLM.
    Can be used for background title enhancement if needed.
    """
    try:
        title_chain = (
            ChatPromptTemplate.from_messages([
                ("human", "Summarize in 3-5 words as a chat title (no quotes):\n{question}\n\nTitle:"),
            ])
            | get_llm("chat_title", temperature=0.3, max_tokens=20)
            | StrOutputParser()
        )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: title_chain.invoke({"question": question[:200]})
        )
        title = response.strip().strip('"').strip("'")
        return title if title else generate_chat_preview_sync(question)
    except Exception as e:
        logger.debug(f"LLM title generation failed, using fallback: {e}")
        return generate_chat_preview_sync(question)
