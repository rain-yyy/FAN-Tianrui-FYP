"""
OpenRouter 嵌入模型工具，供 kb_loader 和 vector_store 共用。


OpenAI-compatible APIs（非官方 api.openai.com 的网关，如 OpenRouter、自建 v1 代理）
---------------------------------------------------------------------------
部分兼容层对 embeddings 响应的处理与官方不一致（例如默认编码、省略 ``data`` 等）。
本模块在请求 **第三方兼容端点** 时会显式传入 ``encoding_format="float"``，以降低空向量列表的概率。

若你改用 **LangChain** 的 ``OpenAIEmbeddings`` 并指向同一类网关，建议在构造参数中设置：

- ``encoding_format="float"``（若该类支持该参数）
- ``check_embedding_ctx_length=False``，避免仅用本地 tokenizer 预估长度导致误拦或与网关计费不一致

官方 OpenAI ``https://api.openai.com/v1`` 仍走默认请求参数（与历史行为一致）。
"""
import os
import time
import logging
from typing import List, Any
from urllib.parse import urlparse

from openai import OpenAI
from langchain_core.embeddings import Embeddings
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("app.ingestion.embedding_utils")

OPENROUTER_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"

# 仅此 hostname 视为「官方 OpenAI API」；其余（含 Azure 自定义域、OpenRouter、本地 litellm 等）按兼容网关处理。
_OPENAI_OFFICIAL_API_HOSTS = frozenset({"api.openai.com"})


def is_third_party_openai_compatible_api(base_url: str) -> bool:
    """
    若 base_url 不是官方 OpenAI API，则为 True（需显式 ``encoding_format="float"`` 等兼容处理）。
    """
    parsed = urlparse((base_url or "").strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return True
    return host not in _OPENAI_OFFICIAL_API_HOSTS


def _normalize_base_url(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _actionable_third_party_no_data_error(base_url: str) -> ValueError:
    return ValueError(
        "No embedding data received. When using OpenAI-compatible APIs (e.g. OpenRouter), "
        "some gateways omit or encode vectors differently. Mitigations: call "
        "embeddings.create(..., encoding_format='float'); if you use LangChain OpenAIEmbeddings "
        "against the same base URL, set check_embedding_ctx_length=False and encoding_format='float' "
        f"where supported. (base_url={base_url!r})"
    )


def _raise_no_embedding_data(*, base_url: str, raw_response: Any, batch_label: str) -> None:
    logger.error("Embeddings API returned empty data (%s): %s", batch_label, raw_response)
    if is_third_party_openai_compatible_api(base_url):
        raise _actionable_third_party_no_data_error(base_url)
    raise ValueError(
        "No embedding data received from the embedding provider. "
        f"(base_url={base_url!r}, {batch_label})"
    )


def _should_rewrap_no_embedding_error(exc: BaseException, base_url: str) -> bool:
    if not is_third_party_openai_compatible_api(base_url):
        return False
    return "no embedding data received" in str(exc).lower()


class OpenRouterEmbeddings(Embeddings):
    """
    专为 OpenRouter 优化的 Embedding 实现。
    解决了 LangChain OpenAIEmbeddings 默认发送 Token IDs 导致 OpenRouter 报错的问题。

    OpenAI-compatible APIs
    ----------------------
    见模块级 docstring：第三方 ``base_url`` 会在 ``embeddings.create`` 上附加 ``encoding_format='float'``；
    若仍返回空 ``data``，将抛出带排障建议的 ``ValueError``（含 ``check_embedding_ctx_length`` 等说明）。
    """

    def __init__(self, model: str, api_key: str, base_url: str):
        self.model = model
        self._base_url = _normalize_base_url(base_url)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/FAN-Tianrui-FYP",
                "X-Title": "FYP Wiki Generator",
            },
        )

    # 每次实际发送给 OpenRouter API 的最大文本条数（降低以缓解 504/无响应问题）
    INNER_BATCH_SIZE = 20
    # 两次 API 请求之间的冷却时间（秒）
    INNER_BATCH_SLEEP_SEC = 1.0

    def _embeddings_create(self, input_payload: List[str], batch_label: str):
        kwargs: dict = {"model": self.model, "input": input_payload}
        if is_third_party_openai_compatible_api(self._base_url):
            kwargs["encoding_format"] = "float"

        logger.debug(
            "[embed] → API call | %s | model=%s | texts=%d | base_url=%s | encoding_format=%s",
            batch_label,
            kwargs.get("model"),
            len(input_payload),
            self._base_url,
            kwargs.get("encoding_format", "default"),
        )
        t0 = time.monotonic()
        try:
            response = self.client.embeddings.create(**kwargs)
            elapsed = time.monotonic() - t0
            data_len = len(response.data) if response.data else 0
            logger.debug(
                "[embed] ← API response | %s | elapsed=%.2fs | data_items=%d | model=%s | usage=%s",
                batch_label,
                elapsed,
                data_len,
                response.model,
                response.usage,
            )
            if not response.data:
                logger.error(
                    "[embed] !! Empty data in response | %s | full_response=%r",
                    batch_label,
                    response,
                )
            return response
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "[embed] !! Exception | %s | elapsed=%.2fs | type=%s | msg=%s",
                batch_label,
                elapsed,
                type(e).__name__,
                e,
            )
            if _should_rewrap_no_embedding_error(e, self._base_url):
                raise _actionable_third_party_no_data_error(self._base_url) from e
            raise

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """将一批文档转换为向量（内层小批，每批 INNER_BATCH_SIZE 条）。"""
        if not texts:
            return []

        processed_texts = [t if t.strip() else " " for t in texts]
        total_inner = len(processed_texts)
        inner_batch_size = self.INNER_BATCH_SIZE
        total_inner_batches = (total_inner + inner_batch_size - 1) // inner_batch_size

        logger.info(
            "[embed] embed_documents: total_texts=%d, inner_batch_size=%d, inner_batches=%d",
            total_inner, inner_batch_size, total_inner_batches,
        )

        embeddings: List[List[float]] = []
        for inner_idx, i in enumerate(range(0, total_inner, inner_batch_size), start=1):
            batch = processed_texts[i : i + inner_batch_size]
            batch_label = f"inner {inner_idx}/{total_inner_batches} (offset={i}, size={len(batch)})"

            # 预览前两条文本（截断到 80 字符），便于判断是否有空文本/异常字符
            previews = [repr(t[:80]) for t in batch[:2]]
            logger.debug("[embed] %s | text_previews=%s", batch_label, previews)

            response = self._embeddings_create(batch, batch_label)
            if not response.data:
                _raise_no_embedding_data(
                    base_url=self._base_url,
                    raw_response=response,
                    batch_label=batch_label,
                )

            vectors = [r.embedding for r in response.data]
            # 向量维度健全性检查
            if vectors:
                dim = len(vectors[0])
                logger.debug("[embed] %s | vector_dim=%d | vectors_returned=%d", batch_label, dim, len(vectors))

            embeddings.extend(vectors)

            if inner_idx < total_inner_batches:
                time.sleep(self.INNER_BATCH_SLEEP_SEC)

        logger.info("[embed] embed_documents done: total_vectors=%d", len(embeddings))
        return embeddings

    def embed_query(self, text: str) -> List[float]:
        """将单个查询转换为向量。"""
        processed_text = text if text.strip() else " "
        batch_label = "embed_query single"
        logger.debug("[embed] embed_query | text_preview=%r", processed_text[:80])
        response = self._embeddings_create([processed_text], batch_label)
        if not response.data:
            _raise_no_embedding_data(
                base_url=self._base_url,
                raw_response=response,
                batch_label=batch_label,
            )
        return response.data[0].embedding


def get_openrouter_embeddings() -> Embeddings:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("未检测到 OPENROUTER_API_KEY，请设置环境变量。")
    return OpenRouterEmbeddings(
        model=OPENROUTER_EMBEDDING_MODEL,
        api_key=api_key,
        base_url=OPENROUTER_API_BASE,
    )
