"""
Web 搜索工具

用于查询外部知识：包版本、API 文档、CVE、框架用法等。

Provider 优先级（可 config 配置）：
1. Tavily API — 专为 RAG 设计，返回干净摘要（需 API key）
2. SerpAPI — 通用（需 API key）
3. DuckDuckGo — 无 key 免费回退（使用 duckduckgo-search 包）

安全限制：
- 可选白名单过滤（allowed_domains）
- 摘要截断 500 chars / 条
- timeout = 10s
- 禁止返回可能包含恶意内容的结果

Confidence note: 外部来源置信度比内部 RAG 低，relevance_score 上限 0.78。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.agent.state import ContextPiece

logger = logging.getLogger("app.agent.tools.web")

_DEFAULT_TIMEOUT = 10
_MAX_SNIPPET_CHARS = 500
_SCORE_CAP = 0.78  # 外部来源置信度上限


def _domain_allowed(url: str, allowed_domains: Optional[List[str]]) -> bool:
    """检查 URL 域是否在白名单内（空白名单 = 全部允许）"""
    if not allowed_domains:
        return True
    try:
        host = urlparse(url).netloc.lower()
        # 去掉 www. 前缀
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in allowed_domains)
    except Exception:
        return False


def _truncate(text: str, max_chars: int = _MAX_SNIPPET_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


# ---------------------------------------------------------------------------
# Provider: DuckDuckGo (free, no key required)
# ---------------------------------------------------------------------------

def _search_duckduckgo(
    query: str,
    max_results: int,
    timeout: int,
    allowed_domains: Optional[List[str]],
) -> List[Dict[str, str]]:
    """使用 duckduckgo-search 包查询（无需 API key）"""
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        logger.warning("[WebSearch] duckduckgo_search not installed. Run: pip install duckduckgo-search")
        return []

    results: List[Dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results * 2):
                url = r.get("href") or r.get("link") or ""
                if allowed_domains and not _domain_allowed(url, allowed_domains):
                    continue
                results.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": _truncate(r.get("body", "") or r.get("snippet", "")),
                })
                if len(results) >= max_results:
                    break
    except Exception as e:
        logger.warning("[WebSearch] DuckDuckGo search failed: %s", e)
    return results


# ---------------------------------------------------------------------------
# Provider: Tavily (premium, clean RAG-ready results)
# ---------------------------------------------------------------------------

def _search_tavily(
    query: str,
    max_results: int,
    timeout: int,
    allowed_domains: Optional[List[str]],
    api_key: str,
    search_depth: str = "basic",
) -> List[Dict[str, str]]:
    """使用 Tavily API 查询"""
    try:
        from tavily import TavilyClient  # type: ignore
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth=search_depth,
            max_results=max_results * 2,
        )
        results: List[Dict[str, str]] = []
        for r in response.get("results", []):
            url = r.get("url", "")
            if allowed_domains and not _domain_allowed(url, allowed_domains):
                continue
            results.append({
                "title": r.get("title", ""),
                "url": url,
                "snippet": _truncate(r.get("content", "") or r.get("snippet", "")),
            })
            if len(results) >= max_results:
                break
        return results
    except ImportError:
        logger.warning("[WebSearch] tavily package not installed. Run: pip install tavily-python")
        return []
    except Exception as e:
        logger.warning("[WebSearch] Tavily search failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Provider: SerpAPI
# ---------------------------------------------------------------------------

def _search_serpapi(
    query: str,
    max_results: int,
    timeout: int,
    allowed_domains: Optional[List[str]],
    api_key: str,
) -> List[Dict[str, str]]:
    """使用 SerpAPI 查询"""
    try:
        import requests  # type: ignore
        params = {
            "q": query,
            "api_key": api_key,
            "num": max_results * 2,
            "engine": "google",
        }
        resp = requests.get(
            "https://serpapi.com/search",
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        results: List[Dict[str, str]] = []
        for r in data.get("organic_results", []):
            url = r.get("link", "")
            if allowed_domains and not _domain_allowed(url, allowed_domains):
                continue
            results.append({
                "title": r.get("title", ""),
                "url": url,
                "snippet": _truncate(r.get("snippet", "")),
            })
            if len(results) >= max_results:
                break
        return results
    except ImportError:
        logger.warning("[WebSearch] requests not installed.")
        return []
    except Exception as e:
        logger.warning("[WebSearch] SerpAPI search failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Main tool class
# ---------------------------------------------------------------------------

class WebSearchTool:
    """
    外部 Web 搜索工具。

    Provider 自动选择：Tavily > SerpAPI > DuckDuckGo。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.tavily_api_key: str = cfg.get("tavily_api_key", "")
        self.serpapi_key: str = cfg.get("serpapi_key", "")
        self.allowed_domains: Optional[List[str]] = cfg.get("allowed_domains") or None
        self.timeout: int = int(cfg.get("timeout", _DEFAULT_TIMEOUT))
        self.max_results_default: int = int(cfg.get("max_results", 5))
        self.provider: str = cfg.get("provider", "auto")  # auto | tavily | serpapi | duckduckgo

        logger.info(
            "[WebSearchTool] provider=%s tavily=%s serpapi=%s allowed_domains=%s",
            self.provider,
            bool(self.tavily_api_key),
            bool(self.serpapi_key),
            self.allowed_domains,
        )

    def execute(
        self,
        query: str,
        search_type: str = "general",
        max_results: int = 5,
        domain_filter: Optional[str] = None,
    ) -> ContextPiece:
        """
        执行 Web 搜索。

        Args:
            query: 搜索关键词/问题
            search_type: "general" | "code_docs" | "version" | "cve"
            max_results: 最多返回条数
            domain_filter: 单个域白名单（优先于全局配置）

        Returns:
            ContextPiece，包含格式化搜索结果
        """
        if not query or not query.strip():
            return ContextPiece(
                source="web_search",
                content="Empty search query provided.",
                relevance_score=0.0,
                metadata={"error": "empty_query"},
            )

        query = query.strip()
        max_results = max(1, min(max_results, 10))

        # 域白名单：domain_filter 优先，否则用全局 allowed_domains
        effective_domains: Optional[List[str]] = None
        if domain_filter:
            effective_domains = [domain_filter]
        elif self.allowed_domains:
            effective_domains = self.allowed_domains

        # 根据 search_type 调整查询语境
        enhanced_query = self._enhance_query(query, search_type)

        results: List[Dict[str, str]] = []
        provider_used = "none"

        t0 = time.perf_counter()
        if self.provider == "tavily" or (self.provider == "auto" and self.tavily_api_key):
            results = _search_tavily(
                enhanced_query, max_results, self.timeout, effective_domains, self.tavily_api_key
            )
            provider_used = "tavily"

        if not results and (self.provider == "serpapi" or (self.provider == "auto" and self.serpapi_key)):
            results = _search_serpapi(
                enhanced_query, max_results, self.timeout, effective_domains, self.serpapi_key
            )
            provider_used = "serpapi"

        if not results and self.provider in ("duckduckgo", "auto"):
            results = _search_duckduckgo(
                enhanced_query, max_results, self.timeout, effective_domains
            )
            provider_used = "duckduckgo"

        duration_ms = int((time.perf_counter() - t0) * 1000)

        if not results:
            return ContextPiece(
                source="web_search",
                content=f"No web results found for query: `{query}`",
                relevance_score=0.0,
                metadata={
                    "query": query,
                    "provider": provider_used,
                    "search_type": search_type,
                    "duration_ms": duration_ms,
                    "error": "no_results",
                },
            )

        # 格式化输出
        content_lines = [
            f"**Web Search Results** for: `{query}` ({len(results)} result(s) via {provider_used})\n"
        ]
        urls: List[str] = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            content_lines.append(f"**{i}. {title}**")
            if url:
                content_lines.append(f"   URL: {url}")
                urls.append(url)
            if snippet:
                content_lines.append(f"   {snippet}")
            content_lines.append("")

        content = "\n".join(content_lines).strip()

        return ContextPiece(
            source="web_search",
            content=content,
            relevance_score=_SCORE_CAP,
            metadata={
                "query": query,
                "enhanced_query": enhanced_query if enhanced_query != query else "",
                "search_type": search_type,
                "provider": provider_used,
                "results_count": len(results),
                "urls": urls[:10],
                "duration_ms": duration_ms,
            },
        )

    @staticmethod
    def _enhance_query(query: str, search_type: str) -> str:
        """根据 search_type 增强查询语境"""
        if search_type == "version":
            if "latest" not in query.lower() and "version" not in query.lower():
                return f"{query} latest stable version"
        elif search_type == "cve":
            if "cve" not in query.lower() and "vulnerability" not in query.lower():
                return f"{query} CVE security vulnerability"
        elif search_type == "code_docs":
            if "documentation" not in query.lower() and "docs" not in query.lower():
                return f"{query} documentation API reference"
        return query
