-- Victoria Albright — Postgres schema
--
-- Three logical surfaces:
--   1. conversations  - one row per chat session
--   2. messages       - one row per user/assistant turn
--   3. knowledge_gaps - filed when Victoria escalates a question
--   4. rag_chunks     - pgvector index built by scripts/build_rag_index.py
--
-- This file is idempotent. Re-run safely after schema changes.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- conversations -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_agent    TEXT,
    page_url      TEXT
);

CREATE INDEX IF NOT EXISTS conversations_last_seen_idx
    ON conversations (last_seen_at DESC);

-- messages ------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
    content         TEXT NOT NULL,
    source          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS messages_conv_created_idx
    ON messages (conversation_id, created_at);

-- knowledge_gaps ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id     UUID REFERENCES conversations(id) ON DELETE SET NULL,
    question            TEXT NOT NULL,
    confidence          REAL NOT NULL,
    suggested_direction TEXT,
    github_issue_url    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS knowledge_gaps_created_idx
    ON knowledge_gaps (created_at DESC);

-- rag_chunks (pgvector) -----------------------------------------------------
-- The dim must match settings.embedding_dim. Default is 1024 (e5-large).

CREATE TABLE IF NOT EXISTS rag_chunks (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1024),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- IVFFlat index for cosine distance. lists=100 is a reasonable default
-- for ~10k chunks; tune up as the corpus grows.
CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx
    ON rag_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS rag_chunks_url_idx
    ON rag_chunks (url);
