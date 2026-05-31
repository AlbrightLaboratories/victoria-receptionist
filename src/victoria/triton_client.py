"""Async HTTP client for the upstream LLM (Triton / OpenAI-compat router).

We support two Triton fronting patterns:

1. **OpenAI-compatible router** (vLLM, Ollama, NIM) at
   `POST {base}/v1/chat/completions` — preferred. Set
   `TRITON_OPENAI_COMPAT=true`.
2. **Raw Triton generate** at
   `POST {base}/v2/models/<model>/generate` — fallback for plain
   Triton with a generate-style backend.

Either way, the public surface is `generate(system, user_messages)`
returning a string. If the upstream is unreachable, we raise
`TritonUpstreamError` and the conversation engine degrades gracefully.

Pattern mirrors `scripts/boardroom/ollama_client.py` in the main
repo — same stdlib-ish discipline, but async via httpx since we're
inside a FastAPI process and don't want to block the loop.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class TritonUpstreamError(RuntimeError):
    """Raised when the upstream LLM is unreachable or returns a non-2xx."""


class TritonClient:
    """Thin async client. Caller owns the lifecycle; close on shutdown."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.triton_url.rstrip("/"),
            timeout=settings.llm_timeout_s,
        )

    async def close(self) -> None:
        """Release the underlying connection pool. Call from FastAPI lifespan."""
        await self._client.aclose()

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        *,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Generate one reply. Returns plain text; raises on upstream failure."""
        if self._settings.triton_openai_compat:
            return await self._openai_chat(system_prompt, user_message, history or [])
        return await self._triton_generate(system_prompt, user_message, history or [])

    # --- OpenAI-compat path ---------------------------------------------------

    async def _openai_chat(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
    ) -> str:
        """Call `/v1/chat/completions`. Standard OpenAI message schema."""
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        payload = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": self._settings.llm_temperature,
            "max_tokens": self._settings.llm_max_tokens,
            "stream": False,
        }
        try:
            resp = await self._client.post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise TritonUpstreamError(f"LLM upstream unreachable: {e}") from e
        if resp.status_code >= 400:
            raise TritonUpstreamError(
                f"LLM upstream returned {resp.status_code}: {resp.text[:200]}"
            )
        body: dict[str, Any] = resp.json()
        try:
            return body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as e:
            raise TritonUpstreamError(f"unexpected LLM payload shape: {e}") from e

    # --- Raw Triton path ------------------------------------------------------

    async def _triton_generate(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
    ) -> str:
        """Call `/v2/models/<model>/generate`. Best-effort prompt concat.

        Triton's generate endpoint expects a flat `text_input`. We render
        the chat as plain text using a Llama-3-ish template; if your
        deployed model needs a different template, override here.
        """
        rendered = self._render_chat(system_prompt, history, user_message)
        payload = {
            "text_input": rendered,
            "max_tokens": self._settings.llm_max_tokens,
            "temperature": self._settings.llm_temperature,
        }
        url = f"/v2/models/{self._settings.llm_model}/generate"
        try:
            resp = await self._client.post(url, json=payload)
        except httpx.HTTPError as e:
            raise TritonUpstreamError(f"Triton unreachable: {e}") from e
        if resp.status_code >= 400:
            raise TritonUpstreamError(
                f"Triton returned {resp.status_code}: {resp.text[:200]}"
            )
        body: dict[str, Any] = resp.json()
        text = body.get("text_output") or body.get("output") or ""
        if not text:
            raise TritonUpstreamError("Triton returned empty text_output")
        return text.strip()

    @staticmethod
    def _render_chat(
        system: str,
        history: list[dict[str, str]],
        user: str,
    ) -> str:
        """Minimal Llama-3 chat-template renderer for non-OpenAI Triton."""
        parts = [
            "<|begin_of_text|>",
            f"<|start_header_id|>system<|end_header_id|>\n{system}\n<|eot_id|>",
        ]
        for m in history:
            parts.append(
                f"<|start_header_id|>{m['role']}<|end_header_id|>\n"
                f"{m['content']}\n<|eot_id|>"
            )
        parts.append(
            f"<|start_header_id|>user<|end_header_id|>\n{user}\n<|eot_id|>"
        )
        parts.append("<|start_header_id|>assistant<|end_header_id|>\n")
        return "".join(parts)
