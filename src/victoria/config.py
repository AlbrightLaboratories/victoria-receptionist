"""Environment-driven configuration for Victoria.

All runtime knobs live here. We use pydantic-settings so values come
from environment variables (or a `.env` file in local dev) and are
validated/typed at boot, not at first-request time.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Victoria service.

    Read from environment first, then `.env` if present. Sensible defaults
    are chosen so a fresh `docker compose up` runs in local/mock mode
    without any secrets configured.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- Runtime mode -----
    # `local` = use mock LLM, no Triton, no GitHub, no web search.
    # `production` = wire everything up. `staging` = same as production
    # but does not actually create GitHub issues (logs intent instead).
    mode: Literal["local", "staging", "production"] = Field(default="local")

    # ----- HTTP server -----
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080)
    log_level: str = Field(default="info")

    # ----- Triton / LLM upstream -----
    # In-cluster default. Override in `.env` for laptop testing.
    triton_url: str = Field(
        default="http://triton-inference.triton-inference.svc.cluster.local:8000"
    )
    # If Triton is fronted by an OpenAI-compatible router (e.g. vLLM, Ollama),
    # set this to `True` and we will call `/v1/chat/completions` instead of
    # `/v2/models/<model>/generate`.
    triton_openai_compat: bool = Field(default=True)
    llm_model: str = Field(default="llama3.1:8b-instruct")
    llm_timeout_s: float = Field(default=60.0)
    llm_max_tokens: int = Field(default=512)
    llm_temperature: float = Field(default=0.4)

    # ----- Embeddings (RAG index build) -----
    # Pointed at a NeMo Embedding endpoint by default; any OpenAI-compatible
    # embeddings endpoint will work.
    embedding_url: str = Field(
        default="http://nemo-embedding.triton-inference.svc.cluster.local:8000"
    )
    embedding_model: str = Field(default="intfloat/e5-large-v2")
    embedding_dim: int = Field(default=1024)

    # ----- Postgres / pgvector -----
    database_url: str = Field(
        default="postgresql://victoria:victoria@postgres:5432/victoria"
    )
    rag_top_k: int = Field(default=4)
    rag_confidence_threshold: float = Field(default=0.72)

    # ----- Web search fallback -----
    tavily_api_key: str | None = Field(default=None)
    searxng_url: str = Field(
        default="http://searxng.search.svc.cluster.local:8080"
    )

    # ----- GitHub knowledge-gap issue creation -----
    gh_repo: str = Field(default="AlbrightLaboratories/albrightlaboratories-dot-com")
    gh_token: str | None = Field(default=None)
    gh_gap_label: str = Field(default="victoria-knowledge-gap")

    # ----- CORS / widget -----
    # Origins permitted to hit the API from the browser.
    allowed_origins: str = Field(
        default="https://albrightlab.com,https://www.albrightlab.com,http://localhost:8080"
    )

    @property
    def allowed_origins_list(self) -> list[str]:
        """Split the CSV env var into a clean list."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_local(self) -> bool:
        """True when we should use the mock LLM and skip cluster calls."""
        return self.mode == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor so we parse env once per process."""
    return Settings()
