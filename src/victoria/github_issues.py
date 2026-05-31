"""Open GitHub issues when Victoria hits a knowledge gap.

When neither the RAG index nor a web-search-and-rebrand pass yields a
confident answer, the conversation engine asks this module to file an
issue at `gh_repo` so the team can add the missing content.

We prefer the `gh` CLI when it's on PATH (the cluster image installs it)
because it handles auth via mounted token cleanly. We fall back to a
direct REST call if `gh` isn't available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class GitHubIssueClient:
    """Thin wrapper that opens `[victoria-gap]` issues."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(timeout=20.0)
        self._gh_path = shutil.which("gh")

    async def close(self) -> None:
        await self._http.aclose()

    async def create_gap_issue(
        self,
        *,
        question: str,
        confidence: float,
        suggested_direction: str,
    ) -> str | None:
        """Create the issue and return its URL. Returns None if disabled.

        We never raise — a failure to file an issue should not break the
        user-facing conversation.
        """
        if self._settings.is_local:
            log.info("[mock-gh] would file gap issue: %s", question[:80])
            return None
        if self._settings.mode == "staging":
            log.info("[staging] would file gap issue: %s", question[:80])
            return None

        title = f"[victoria-gap] {question[:200]}"
        body = self._render_body(question, confidence, suggested_direction)
        label = self._settings.gh_gap_label

        if self._gh_path:
            return await self._create_via_cli(title, body, label)
        if self._settings.gh_token:
            return await self._create_via_api(title, body, label)
        log.warning("no gh CLI and no GH_TOKEN — skipping issue creation")
        return None

    @staticmethod
    def _render_body(question: str, confidence: float, direction: str) -> str:
        """Markdown body for the gap issue."""
        return (
            "Victoria could not answer a visitor question with sufficient "
            "confidence from RAG or web search.\n\n"
            f"**Visitor question:**\n> {question}\n\n"
            f"**Victoria's confidence:** {confidence:.2f}\n\n"
            "**Suggested research direction (machine-generated):**\n"
            f"> {direction}\n\n"
            "---\n"
            "_Filed automatically by Victoria. Close after the underlying "
            "page or knowledge-source is updated and the RAG index has "
            "been rebuilt (`scripts/build_rag_index.py`)._\n"
        )

    async def _create_via_cli(
        self,
        title: str,
        body: str,
        label: str,
    ) -> str | None:
        """Shell out to `gh issue create --json url`."""
        cmd = [
            self._gh_path or "gh",
            "issue",
            "create",
            "--repo",
            self._settings.gh_repo,
            "--title",
            title,
            "--body",
            body,
            "--label",
            label,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
        except OSError as e:
            log.warning("gh CLI launch failed: %s", e)
            return None
        if proc.returncode != 0:
            log.warning("gh CLI failed (%s): %s", proc.returncode, err.decode().strip())
            return None
        # gh prints the URL on stdout.
        url = out.decode().strip().splitlines()[-1] if out else None
        return url

    async def _create_via_api(
        self,
        title: str,
        body: str,
        label: str,
    ) -> str | None:
        """Fallback: direct REST call to api.github.com."""
        url = f"https://api.github.com/repos/{self._settings.gh_repo}/issues"
        headers = {
            "Authorization": f"token {self._settings.gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "labels": [label],
        }
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            log.warning("github api request failed: %s", e)
            return None
        if resp.status_code >= 300:
            log.warning(
                "github api returned %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            return None
        try:
            return resp.json().get("html_url")
        except json.JSONDecodeError:
            return None
