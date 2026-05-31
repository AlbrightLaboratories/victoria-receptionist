#!/usr/bin/env python3
"""Build (or rebuild) the pgvector RAG index over AlbrightLab site content.

Walks a starting URL, follows in-domain links, extracts visible text,
chunks it, embeds each chunk via the configured embedding endpoint,
and UPSERTs rows into `rag_chunks`.

Idempotent: rows are deduped on (url, content_hash) — re-running just
refreshes embeddings for changed chunks.

Usage:
    python scripts/build_rag_index.py --start https://albrightlab.com
    python scripts/build_rag_index.py --local-mirror ./site-mirror

Phase 2 will replace this with a NeMo Curator pipeline. The schema and
table contract (`rag_chunks(url, title, content, embedding)`) is the
contract that won't change.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import asyncpg
import httpx

# Allow `from victoria.config import ...` when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from victoria.config import get_settings  # noqa: E402

log = logging.getLogger("build_rag_index")

# Strip <script>, <style>, and HTML tags. We deliberately do NOT pull in
# BeautifulSoup for this — the dependency surface is already big enough.
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

CHUNK_SIZE = 1200  # characters per chunk; ~ 250-300 tokens
CHUNK_OVERLAP = 200


def strip_html(html: str) -> tuple[str, str]:
    """Return (title, plain_text)."""
    title_match = _TITLE_RE.search(html)
    title = (title_match.group(1) if title_match else "").strip() or "(untitled)"
    no_scripts = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", no_scripts)
    text = _WS_RE.sub(" ", text).strip()
    return title, text


def extract_links(base_url: str, html: str) -> list[str]:
    """Pull same-origin links out of the page."""
    base_host = urlparse(base_url).netloc
    out: list[str] = []
    for raw in _HREF_RE.findall(html):
        joined = urljoin(base_url, raw)
        joined, _ = urldefrag(joined)
        if urlparse(joined).netloc == base_host and joined.startswith("http"):
            out.append(joined)
    return out


def chunk_text(text: str) -> list[str]:
    """Split into overlapping windows. Naive — good enough for v1."""
    if len(text) <= CHUNK_SIZE:
        return [text] if text else []
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + CHUNK_SIZE])
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


async def embed_one(client: httpx.AsyncClient, model: str, text: str) -> list[float]:
    """Hit the embedding endpoint for one chunk."""
    resp = await client.post(
        "/v1/embeddings", json={"input": text, "model": model}
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


async def upsert_chunk(
    conn: asyncpg.Connection,
    *,
    url: str,
    title: str,
    content: str,
    embedding: list[float],
) -> None:
    """INSERT a chunk, dedup'd via (url, content-hash) prefix in content."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    # Tag the content with the digest so re-runs replace cleanly.
    tagged_content = f"[{digest}] {content}"
    await conn.execute(
        """
        DELETE FROM rag_chunks WHERE url = $1 AND content LIKE $2
        """,
        url,
        f"[{digest}] %",
    )
    await conn.execute(
        """
        INSERT INTO rag_chunks (url, title, content, embedding)
        VALUES ($1, $2, $3, $4::vector)
        """,
        url,
        title,
        tagged_content,
        embedding,
    )


async def crawl_and_index(start_url: str, max_pages: int = 200) -> None:
    """Walk the site BFS-style, indexing each visited page."""
    settings = get_settings()
    visited: set[str] = set()
    queue: list[str] = [start_url]

    pool = await asyncpg.create_pool(dsn=settings.database_url)
    fetch = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    embed = httpx.AsyncClient(
        base_url=settings.embedding_url.rstrip("/"), timeout=30.0
    )

    indexed = 0
    try:
        while queue and indexed < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                resp = await fetch.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("skip %s: %s", url, e)
                continue
            html = resp.text
            title, text = strip_html(html)
            if not text:
                continue

            chunks = chunk_text(text)
            async with pool.acquire() as conn:
                for chunk in chunks:
                    try:
                        emb = await embed_one(
                            embed, settings.embedding_model, chunk
                        )
                        await upsert_chunk(
                            conn,
                            url=url,
                            title=title,
                            content=chunk,
                            embedding=emb,
                        )
                    except (httpx.HTTPError, asyncpg.PostgresError) as e:
                        log.warning("chunk-write failed for %s: %s", url, e)
            indexed += 1
            log.info("indexed %s (%d chunks)", url, len(chunks))

            for link in extract_links(url, html):
                if link not in visited:
                    queue.append(link)
    finally:
        await fetch.aclose()
        await embed.aclose()
        await pool.close()
    log.info("done: %d pages indexed (%d visited)", indexed, len(visited))


async def index_local_mirror(mirror_dir: Path) -> None:
    """Index a local directory of HTML files (offline build)."""
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url)
    embed = httpx.AsyncClient(
        base_url=settings.embedding_url.rstrip("/"), timeout=30.0
    )
    try:
        for html_path in mirror_dir.rglob("*.html"):
            html = html_path.read_text(encoding="utf-8", errors="ignore")
            title, text = strip_html(html)
            if not text:
                continue
            rel_url = "/" + str(html_path.relative_to(mirror_dir)).replace(
                "index.html", ""
            )
            async with pool.acquire() as conn:
                for chunk in chunk_text(text):
                    emb = await embed_one(
                        embed, settings.embedding_model, chunk
                    )
                    await upsert_chunk(
                        conn,
                        url=rel_url,
                        title=title,
                        content=chunk,
                        embedding=emb,
                    )
            log.info("indexed %s", rel_url)
    finally:
        await embed.aclose()
        await pool.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", default="https://albrightlab.com", help="seed URL"
    )
    parser.add_argument(
        "--local-mirror",
        type=Path,
        default=None,
        help="path to a local site mirror (skips network crawl)",
    )
    parser.add_argument("--max-pages", type=int, default=200)
    args = parser.parse_args()

    if args.local_mirror:
        asyncio.run(index_local_mirror(args.local_mirror))
    else:
        asyncio.run(crawl_and_index(args.start, args.max_pages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
