"""Final-pass rewriter — takes a web-search-derived draft, returns it
in Victoria's voice with source attribution preserved.

The rebrand pass runs after the web search returns hits but before we
send the reply to the user. It exists because raw web-search snippets
read like marketing copy and don't match the AlbrightLab tone.
"""
from __future__ import annotations

import logging
from typing import Iterable

from .config import Settings
from .mock_llm import mock_rebrand
from .personas import REBRAND_SYSTEM_PROMPT
from .triton_client import TritonClient, TritonUpstreamError
from .web_search import WebResult

log = logging.getLogger(__name__)


class Rebrander:
    """Wraps an LLM call with the rebrand system prompt."""

    def __init__(self, settings: Settings, llm: TritonClient) -> None:
        self._settings = settings
        self._llm = llm

    async def rebrand(
        self,
        original_question: str,
        results: list[WebResult],
    ) -> str:
        """Return a polished, voice-matched answer with citations."""
        if not results:
            return (
                "I'm not finding a confident answer for that. Please email "
                "coreymalbright@gmail.com or call (202) 642-6739."
            )

        draft = self._compose_draft(original_question, results)
        sources: Iterable[str] = (r.title or r.url for r in results)

        if self._settings.is_local:
            return mock_rebrand(draft, sources)

        user_prompt = (
            f"Visitor asked: {original_question}\n\n"
            f"Draft assembled from web search:\n{draft}\n\n"
            "Rewrite the draft in Victoria Albright's voice per the system "
            "instructions. Keep the source citation."
        )
        try:
            return await self._llm.generate(REBRAND_SYSTEM_PROMPT, user_prompt)
        except TritonUpstreamError as e:
            log.warning("rebrand LLM call failed: %s; returning draft", e)
            return draft

    @staticmethod
    def _compose_draft(question: str, results: list[WebResult]) -> str:
        """Stitch web snippets into a single draft paragraph + sources."""
        top = results[0]
        body = f"{top.snippet.strip()}"
        if len(results) > 1:
            extras = " ".join(r.snippet.strip() for r in results[1:3])
            body = f"{body} {extras}"
        sources = "; ".join(
            f"{r.title or 'source'} ({r.url})" for r in results[:3] if r.url
        )
        return f"{body}\n\nSources: {sources}"
