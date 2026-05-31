"""Smoke tests — app boots, /health responds, mock LLM produces a reply.

We deliberately avoid touching the network, Postgres, or Triton here.
The whole point of `MODE=local` is that you can run this test suite
on a laptop with no infrastructure at all.
"""
from __future__ import annotations

import os

# Force local mode BEFORE any victoria.* import. get_settings() is cached
# at first call, so the env must be right when that call happens.
os.environ["MODE"] = "local"
os.environ["DATABASE_URL"] = "postgresql://noop:noop@127.0.0.1:1/noop"

from fastapi.testclient import TestClient  # noqa: E402

from victoria.main import app  # noqa: E402
from victoria.mock_llm import mock_generate  # noqa: E402


def test_health_endpoint_returns_ok():
    """The /health route must return 200 even with no upstreams."""
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_mock_llm_greeting():
    """Mock LLM should produce a greeting for 'hello'."""
    reply = mock_generate("hello")
    assert "Victoria" in reply


def test_mock_llm_default_escalates():
    """Unknown questions must escalate to phone/email contacts."""
    reply = mock_generate("what is the airspeed velocity of an unladen swallow")
    assert "(202) 642-6739" in reply or "coreymalbright@gmail.com" in reply


def test_chat_endpoint_returns_reply():
    """POST /api/chat in local mode produces a deterministic mock reply."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            json={"message": "hi there", "page_url": "https://albrightlab.com/"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "mock"
        assert body["conversation_id"]
        assert "Victoria" in body["reply"]


def test_chat_endpoint_rejects_empty_message():
    """Empty messages should be rejected with a 400."""
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "   "})
        assert resp.status_code == 400


def test_widget_assets_are_served():
    """widget.js and widget.css must be reachable from the app."""
    with TestClient(app) as client:
        resp_js = client.get("/widget.js")
        assert resp_js.status_code == 200
        assert "victoria" in resp_js.text.lower()

        resp_css = client.get("/widget.css")
        assert resp_css.status_code == 200
        assert ".victoria-bubble" in resp_css.text
