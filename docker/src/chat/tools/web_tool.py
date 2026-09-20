"""
Web search tool for external knowledge (package versions, API docs, CVEs)
not contained in the repository itself.

Provider priority (config-driven): Tavily > SerpAPI > DuckDuckGo. DuckDuckGo
requires no API key and no config, so with `duckduckgo-search` installed this
tool always returns real results instead of the old silent no-op.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from langchain_core.tools import StructuredTool

from src.chat.tools.schemas import WebSearchArgs
from src.utils.async_utils import run_sync

logger = logging.getLogger("app.chat.tools.web")

_DEFAULT_TIMEOUT = 10
_MAX_SNIPPET_CHARS = 500


def _domain_allowed(url: str, allowed_domains: Optional[List[str]]) -> bool:
    if not allowed_domains:
        return True
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in allowed_domains)
    except Exception:
        return False


def _truncate(text: str, max_chars: int = _MAX_SNIPPET_CHARS) -> str:
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"


def _search_duckduckgo(query: str, max_results: int, allowed_domains: Optional[List[str]]) -> List[Dict[str, str]]:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        logger.warning("duckduckgo_search not installed. Run: pip install duckduckgo-search")
        return []

    results: List[Dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results * 2):
                url = r.get("href") or r.get("link") or ""
                if allowed_domains and not _domain_allowed(url, allowed_domains):
                    continue
                results.append({"title": r.get("title", ""), "url": url, "snippet": _truncate(r.get("body", "") or r.get("snippet", ""))})
                if len(results) >= max_results:
                    break
    except Exception as e:
        logger.warning("DuckDuckGo search failed: %s", e)
    return results


def _search_tavily(query: str, max_results: int, allowed_domains: Optional[List[str]], api_key: str) -> List[Dict[str, str]]:
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(query=query, search_depth="basic", max_results=max_results * 2)
        results: List[Dict[str, str]] = []
        for r in response.get("results", []):
            url = r.get("url", "")
            if allowed_domains and not _domain_allowed(url, allowed_domains):
                continue
            results.append({"title": r.get("title", ""), "url": url, "snippet": _truncate(r.get("content", "") or r.get("snippet", ""))})
            if len(results) >= max_results:
                break
        return results
    except ImportError:
        logger.warning("tavily package not installed. Run: pip install tavily-python")
        return []
    except Exception as e:
        logger.warning("Tavily search failed: %s", e)
        return []


def _search_serpapi(query: str, max_results: int, timeout: int, allowed_domains: Optional[List[str]], api_key: str) -> List[Dict[str, str]]:
    try:
        import requests
        resp = requests.get("https://serpapi.com/search", params={"q": query, "api_key": api_key, "num": max_results * 2, "engine": "google"}, timeout=timeout)
        resp.raise_for_status()
        results: List[Dict[str, str]] = []
        for r in resp.json().get("organic_results", []):
            url = r.get("link", "")
            if allowed_domains and not _domain_allowed(url, allowed_domains):
                continue
            results.append({"title": r.get("title", ""), "url": url, "snippet": _truncate(r.get("snippet", ""))})
            if len(results) >= max_results:
                break
        return results
    except ImportError:
        logger.warning("requests not installed.")
        return []
    except Exception as e:
        logger.warning("SerpAPI search failed: %s", e)
        return []


class WebSearchEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.tavily_api_key: str = cfg.get("tavily_api_key", "")
        self.serpapi_key: str = cfg.get("serpapi_key", "")
        self.allowed_domains: Optional[List[str]] = cfg.get("allowed_domains") or None
        self.timeout: int = int(cfg.get("timeout", _DEFAULT_TIMEOUT))
        self.provider: str = cfg.get("provider", "auto")

    def search(self, query: str, search_type: str = "general", max_results: int = 5, domain_filter: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        if not query or not query.strip():
            return "Empty search query provided.", {"error": "empty_query"}

        query = query.strip()
        max_results = max(1, min(max_results, 10))
        effective_domains = [domain_filter] if domain_filter else self.allowed_domains
        enhanced_query = self._enhance_query(query, search_type)

        results: List[Dict[str, str]] = []
        provider_used = "none"
        t0 = time.perf_counter()

        if self.provider == "tavily" or (self.provider == "auto" and self.tavily_api_key):
            results = _search_tavily(enhanced_query, max_results, effective_domains, self.tavily_api_key)
            provider_used = "tavily"
        if not results and (self.provider == "serpapi" or (self.provider == "auto" and self.serpapi_key)):
            results = _search_serpapi(enhanced_query, max_results, self.timeout, effective_domains, self.serpapi_key)
            provider_used = "serpapi"
        if not results and self.provider in ("duckduckgo", "auto"):
            results = _search_duckduckgo(enhanced_query, max_results, effective_domains)
            provider_used = "duckduckgo"

        duration_ms = int((time.perf_counter() - t0) * 1000)

        if not results:
            return f"No web results found for query: `{query}`", {"query": query, "provider": provider_used, "duration_ms": duration_ms, "error": "no_results"}

        lines = [f"Web search results for: `{query}` ({len(results)} result(s) via {provider_used})\n"]
        urls: List[str] = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', 'No title')}")
            if r.get("url"):
                lines.append(f"   URL: {r['url']}")
                urls.append(r["url"])
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append("")

        return "\n".join(lines).strip(), {
            "query": query, "provider": provider_used, "results_count": len(results),
            "urls": urls[:10], "duration_ms": duration_ms,
        }

    @staticmethod
    def _enhance_query(query: str, search_type: str) -> str:
        lower = query.lower()
        if search_type == "version" and "latest" not in lower and "version" not in lower:
            return f"{query} latest stable version"
        if search_type == "cve" and "cve" not in lower and "vulnerability" not in lower:
            return f"{query} CVE security vulnerability"
        if search_type == "code_docs" and "documentation" not in lower and "docs" not in lower:
            return f"{query} documentation API reference"
        return query


def build_web_search_tool(config: Optional[Dict[str, Any]] = None) -> StructuredTool:
    engine = WebSearchEngine(config)

    def _run(query: str, search_type: str = "general", max_results: int = 5, domain_filter: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        try:
            return engine.search(query, search_type, max_results, domain_filter)
        except Exception as e:
            logger.exception("web_search failed")
            return f"Search failed: {e}", {"error": str(e)}

    async def _arun(query: str, search_type: str = "general", max_results: int = 5, domain_filter: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        return await run_sync(_run, query, search_type, max_results, domain_filter)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="web_search",
        description="Search the web for external knowledge not contained in this repository: package versions, CVEs, API documentation for external libraries.",
        args_schema=WebSearchArgs,
        response_format="content_and_artifact",
    )
