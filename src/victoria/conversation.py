"""Conversation orchestrator — the state machine that decides which
pipeline (mock / RAG / web+rebrand / escalate-and-file-gap) handles
each incoming user message.

Flow:
    user message in
      └─> if MODE=local: mock_llm → reply
      └─> else:
            ├─ RAG search
            │   └─ if confidence >= threshold: LLM(system + RAG context) → reply
            ├─ Web search
            │   └─ if any results: Rebrander → reply
            └─ neither produced confident answer:
                ├─ LLM(GAP_RESEARCH_PROMPT) → research-direction string
                ├─ GitHubIssueClient.create_gap_issue(...)
                └─ polite escalation reply with the issue URL
"""
from __future__ import annotations

import logging
from uuid import UUID, uuid4

from .config import Settings
from .db import Database
from .github_issues import GitHubIssueClient
from .mock_llm import mock_generate
from .models import ChatResponse, Citation
from .personas import GAP_RESEARCH_PROMPT, VICTORIA_SYSTEM_PROMPT
from .rag import RagIndex
from .rebrand import Rebrander
from .triton_client import TritonClient, TritonUpstreamError
from .web_search import WebSearch

log = logging.getLogger(__name__)


class ConversationEngine:
    """Owns the per-request decision logic. Stateless across requests
    apart from the DB and the upstream clients it holds references to.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        llm: TritonClient,
        rag: RagIndex,
        web: WebSearch,
        rebrander: Rebrander,
        github: GitHubIssueClient,
    ) -> None:
        self._settings = settings
        self._db = db
        self._llm = llm
        self._rag = rag
        self._web = web
        self._rebrander = rebrander
        self._github = github

    async def handle(
        self,
        *,
        conversation_id: UUID | None,
        user_message: str,
        page_url: str | None,
    ) -> ChatResponse:
        """Drive one user message through the pipeline."""
        conv_id = conversation_id or uuid4()
        await self._persist_user_turn(conv_id, user_message, page_url)

        # Path 1 — local/mock mode shortcut
        if self._settings.is_local:
            reply = mock_generate(user_message)
            await self._persist_assistant_turn(conv_id, reply, source="mock")
            return ChatResponse(
                conversation_id=conv_id,
                reply=reply,
                source="mock",
                confidence=1.0,
            )

        # Path 2 — RAG
        try:
            hits = await self._rag.search(user_message)
        except Exception as e:  # noqa: BLE001
            log.warning("rag search threw: %s", e)
            hits = []
        if hits and hits[0].confidence >= self._settings.rag_confidence_threshold:
            context = "\n\n".join(
                f"[{h.title} — {h.url}]\n{h.content}" for h in hits
            )
            user_prompt = (
                f"Visitor asked: {user_message}\n\n"
                f"Relevant site content:\n{context}\n\n"
                "Answer using only the site content above. Cite the page "
                "title once."
            )
            try:
                reply = await self._llm.generate(VICTORIA_SYSTEM_PROMPT, user_prompt)
            except TritonUpstreamError as e:
                log.warning("LLM RAG call failed: %s", e)
                reply = self._upstream_fallback_reply()
            citations = [
                Citation(title=h.title, url=h.url, snippet=h.content[:200])
                for h in hits[:3]
            ]
            await self._persist_assistant_turn(conv_id, reply, source="rag")
            return ChatResponse(
                conversation_id=conv_id,
                reply=reply,
                source="rag",
                citations=citations,
                confidence=hits[0].confidence,
            )

        # Path 3 — web search + rebrand
        try:
            web_hits = await self._web.search(user_message)
        except Exception as e:  # noqa: BLE001
            log.warning("web search threw: %s", e)
            web_hits = []
        if web_hits:
            reply = await self._rebrander.rebrand(user_message, web_hits)
            citations = [
                Citation(title=w.title, url=w.url, snippet=w.snippet)
                for w in web_hits[:3]
            ]
            await self._persist_assistant_turn(conv_id, reply, source="web")
            return ChatResponse(
                conversation_id=conv_id,
                reply=reply,
                source="web",
                citations=citations,
                confidence=0.5,  # web-derived; mid-confidence by policy
            )

        # Path 4 — knowledge gap: file an issue and escalate
        direction = await self._suggest_research_direction(user_message)
        issue_url = await self._github.create_gap_issue(
            question=user_message,
            confidence=0.0,
            suggested_direction=direction,
        )
        reply = (
            "I don't have a confident answer for that yet — I've flagged "
            "it for our team. For an immediate response, please email "
            "coreymalbright@gmail.com or call (202) 642-6739."
        )
        await self._persist_assistant_turn(conv_id, reply, source="escalate")
        return ChatResponse(
            conversation_id=conv_id,
            reply=reply,
            source="escalate",
            confidence=0.0,
            knowledge_gap_issue_url=issue_url,
        )

    # --- helpers -------------------------------------------------------------

    async def _suggest_research_direction(self, question: str) -> str:
        """Ask the LLM to describe what content would let Victoria answer
        this next time. Best-effort — falls back to a generic string.
        """
        if self._settings.is_local:
            return (
                "Add a dedicated page or FAQ entry covering this topic, "
                "then rebuild the RAG index."
            )
        try:
            return await self._llm.generate(GAP_RESEARCH_PROMPT, question)
        except TritonUpstreamError:
            return (
                "Unable to generate a specific research direction; please "
                "review the question manually and decide what site content "
                "would resolve it."
            )

    async def _persist_user_turn(
        self,
        conv_id: UUID,
        content: str,
        page_url: str | None,
    ) -> None:
        """INSERT the inbound message. No-op if DB is offline."""
        if not self._db.available:
            return
        await self._db.execute(
            """
            INSERT INTO conversations (id, page_url)
            VALUES ($1, $2)
            ON CONFLICT (id) DO UPDATE SET last_seen_at = NOW()
            """,
            conv_id,
            page_url,
        )
        await self._db.execute(
            """
            INSERT INTO messages (conversation_id, role, content)
            VALUES ($1, 'user', $2)
            """,
            conv_id,
            content,
        )

    async def _persist_assistant_turn(
        self,
        conv_id: UUID,
        content: str,
        *,
        source: str,
    ) -> None:
        """INSERT the outbound reply. No-op if DB is offline."""
        if not self._db.available:
            return
        await self._db.execute(
            """
            INSERT INTO messages (conversation_id, role, content, source)
            VALUES ($1, 'assistant', $2, $3)
            """,
            conv_id,
            content,
            source,
        )

    @staticmethod
    def _upstream_fallback_reply() -> str:
        """User-facing message when Triton is unreachable."""
        return (
            "I'm being onboarded right now and can't reach my answer engine. "
            "Please email coreymalbright@gmail.com or call (202) 642-6739 "
            "and someone will get back to you quickly."
        )
