"""FastAPI entrypoint for Victoria.

Composes the dependency graph in `lifespan()`, mounts the chat route,
and serves the static widget assets so a single container exposes:

    GET  /health              → liveness
    GET  /widget.js           → vanilla-JS injector
    GET  /widget.css          → matching styles
    POST /api/chat            → main chat endpoint
    GET  /api/conversations/{id}  → conversation history (admin)

Run with `uvicorn victoria.main:app --reload` in dev, or via the
Dockerfile's `uvicorn victoria.main:app --host 0.0.0.0 --port 8080`
in production.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .config import Settings, get_settings
from .conversation import ConversationEngine
from .db import Database
from .github_issues import GitHubIssueClient
from .models import ChatRequest, ChatResponse
from .rag import RagIndex
from .rebrand import Rebrander
from .triton_client import TritonClient
from .web_search import WebSearch

log = logging.getLogger(__name__)


# Resolve widget asset paths once. The Dockerfile copies widget.{js,css}
# alongside the package; in dev we fall back to the repo root.
_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent.parent
_WIDGET_JS = _REPO_ROOT / "widget.js"
_WIDGET_CSS = _REPO_ROOT / "widget.css"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire dependencies on startup, tear them down on shutdown."""
    settings: Settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("victoria booting in mode=%s", settings.mode)

    db = Database(settings)
    await db.connect()
    llm = TritonClient(settings)
    rag = RagIndex(settings, db)
    web = WebSearch(settings)
    rebrander = Rebrander(settings, llm)
    github = GitHubIssueClient(settings)
    engine = ConversationEngine(
        settings=settings,
        db=db,
        llm=llm,
        rag=rag,
        web=web,
        rebrander=rebrander,
        github=github,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.engine = engine

    try:
        yield
    finally:
        log.info("victoria shutting down")
        await llm.close()
        await rag.close()
        await web.close()
        await github.close()
        await db.close()


app = FastAPI(
    title="Victoria Albright",
    description="Virtual receptionist for albrightlab.com",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for the widget — origins come from env so we can tighten in prod.
_settings_at_import = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings_at_import.allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Always returns ok if the process is up.

    For readiness (Triton + DB reachable) use `/ready` once it exists —
    intentionally not added yet because the local-mode contract is
    "Victoria serves even when upstreams are down".
    """
    return {"status": "ok"}


@app.get("/widget.js")
async def widget_js() -> FileResponse:
    """Serve the chat-bubble injector JS."""
    if not _WIDGET_JS.exists():
        raise HTTPException(status_code=404, detail="widget.js not packaged")
    return FileResponse(_WIDGET_JS, media_type="application/javascript")


@app.get("/widget.css")
async def widget_css() -> FileResponse:
    """Serve the chat-bubble styles."""
    if not _WIDGET_CSS.exists():
        raise HTTPException(status_code=404, detail="widget.css not packaged")
    return FileResponse(_WIDGET_CSS, media_type="text/css")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Main chat endpoint. One inbound message → one Victoria reply."""
    engine: ConversationEngine = app.state.engine
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    return await engine.handle(
        conversation_id=req.conversation_id,
        user_message=req.message.strip(),
        page_url=req.page_url,
    )


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: UUID) -> JSONResponse:
    """Return the message history for one conversation. Admin / debug."""
    db: Database = app.state.db
    if not db.available:
        return JSONResponse({"conversation_id": str(conv_id), "messages": []})
    rows = await db.fetch(
        """
        SELECT role, content, source, created_at
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at ASC
        """,
        conv_id,
    )
    return JSONResponse(
        {
            "conversation_id": str(conv_id),
            "messages": [
                {
                    "role": r["role"],
                    "content": r["content"],
                    "source": r["source"],
                    "created_at": r["created_at"].isoformat(),
                }
                for r in rows
            ],
        }
    )
