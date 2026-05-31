"""Pydantic data models for the Victoria API.

These models double as request/response schemas and as the typed shape
of rows we shuttle in/out of Postgres. Keeping them small and obvious
on purpose — the conversation state machine is the interesting part.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    """One turn in a conversation."""

    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    role: Role
    content: str
    # Source attribution: "rag", "web:tavily", "web:searxng", "mock", "llm"
    source: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Conversation(BaseModel):
    """A user-facing chat session."""

    id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    user_agent: str | None = None
    page_url: str | None = None


class ChatRequest(BaseModel):
    """Inbound chat from the widget."""

    conversation_id: UUID | None = None
    message: str
    page_url: str | None = None


class Citation(BaseModel):
    """Where the answer came from — surfaced in the widget UI."""

    title: str
    url: str | None = None
    snippet: str | None = None


class ChatResponse(BaseModel):
    """Outbound reply from Victoria."""

    conversation_id: UUID
    reply: str
    source: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0
    knowledge_gap_issue_url: str | None = None


class KnowledgeGap(BaseModel):
    """Internal record of an unanswered question."""

    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    question: str
    confidence: float
    suggested_direction: str
    github_issue_url: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
