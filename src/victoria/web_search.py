"""Web-search fallback. Tavily preferred; SearXNG when no API key.

Why two backends? Tavily gives clean, summarized results out of the box
but requires a paid API key. SearXNG is self-hostable inside the cluster
(`search.svc.cluster.local`) and we control it, but the result quality
is rawer. The conversation engine doesn't care — it just gets a list
of `{title, url, snippet}` dicts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebResult:
    """One result from either Tavily or SearXNG."""

    title: str
    url: str
    snippet: str


class WebSearch:
    """Backend-agnostic web search."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(timeout=20.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def search(self, query: str, *, max_results: int = 5) -> list[WebResult]:
        """Return up to `max_results` hits. Empty list if nothing wired up."""
        if self._settings.is_local:
            return []
        if self._settings.tavily_api_key:
            return await self._tavily(query, max_results)
        return await self._searxng(query, max_results)

    async def _tavily(self, query: str, max_results: int) -> list[WebResult]:
        """Tavily REST — https://docs.tavily.com/docs/rest-api/api-reference."""
        payload = {
            "api_key": self._settings.tavily_api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
            "search_depth": "basic",
        }
        try:
            resp = await self._http.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("tavily request failed: %s", e)
            return []
        body = resp.json()
        return [
            WebResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", "")[:500],
            )
            for r in body.get("results", [])[:max_results]
        ]

    async def _searxng(self, query: str, max_results: int) -> list[WebResult]:
        """SearXNG JSON API — `?format=json&q=...`. Cluster-internal."""
        params = {"q": query, "format": "json"}
        try:
            resp = await self._http.get(
                f"{self._settings.searxng_url.rstrip('/')}/search",
                params=params,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("searxng request failed: %s", e)
            return []
        body = resp.json()
        out: list[WebResult] = []
        for r in body.get("results", [])[:max_results]:
            out.append(
                WebResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=(r.get("content") or "")[:500],
                )
            )
        return out
