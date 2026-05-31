"""pgvector retrieval — given a question, return top-k site-content chunks.

The index is built offline by `scripts/build_rag_index.py`, which walks
the site, chunks HTML, embeds via the configured embedding endpoint, and
INSERTs into `rag_chunks(content, url, title, embedding)`.

At query time we embed the question with the same model and run a
cosine-distance KNN. Confidence is computed as `1 - distance` of the
nearest chunk; the conversation engine decides whether that clears
`rag_confidence_threshold`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import Settings
from .db import Database

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RagHit:
    """One chunk returned by the retriever."""

    content: str
    url: str
    title: str
    distance: float

    @property
    def confidence(self) -> float:
        """Map cosine distance into a 0..1 confidence score."""
        return max(0.0, 1.0 - self.distance)


class RagIndex:
    """Embedding + retrieval surface."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._http = httpx.AsyncClient(
            base_url=settings.embedding_url.rstrip("/"),
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def embed(self, text: str) -> list[float] | None:
        """Call the embedding endpoint. Returns None on failure (local mode)."""
        if self._settings.is_local:
            return None
        payload = {
            "input": text,
            "model": self._settings.embedding_model,
        }
        try:
            resp = await self._http.post("/v1/embeddings", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("embedding upstream failed: %s", e)
            return None
        body = resp.json()
        try:
            return body["data"][0]["embedding"]
        except (KeyError, IndexError):
            log.warning("unexpected embedding payload shape")
            return None

    async def search(self, question: str) -> list[RagHit]:
        """Return top-k chunks for the question. Empty list if no index."""
        if not self._db.available:
            return []
        embedding = await self.embed(question)
        if embedding is None:
            return []
        # pgvector cosine-distance operator: `<=>`
        rows = await self._db.fetch(
            """
            SELECT content, url, title, embedding <=> $1::vector AS distance
            FROM rag_chunks
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            embedding,
            self._settings.rag_top_k,
        )
        return [
            RagHit(
                content=r["content"],
                url=r["url"],
                title=r["title"],
                distance=float(r["distance"]),
            )
            for r in rows
        ]
