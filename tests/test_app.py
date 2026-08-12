"""
End-to-end tests for the FastAPI app: response shapes and the /v1/messages
routing path (with backends stubbed).
"""

from __future__ import annotations

import httpx
import pytest

from gateway.backends.base import SSEEvent
from gateway.main import create_app
from gateway.router import get_router


class _StubBackend:
    provider_name = "groq"
    model_prefix = "claude-groq-"

    async def handle_request(self, model, body, headers, cwd=None):
        yield SSEEvent(event="message_start", data={"type": "message_start", "message": {"id": "msg_x", "model": model, "usage": {"input_tokens": 1, "output_tokens": 0}}})
        yield SSEEvent(event="content_block_start", data={"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        yield SSEEvent(event="content_block_delta", data={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}})
        yield SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0})
        yield SSEEvent(event="message_delta", data={"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}})
        yield SSEEvent(event="message_stop", data={"type": "message_stop"})


@pytest.fixture
def app():
    application = create_app()
    get_router().initialize([_StubBackend()])
    return application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Claude Code Gateway"


@pytest.mark.asyncio
async def test_models_returns_json(client, monkeypatch):
    async def fake_payload(_cfg):
        return {"data": [{"id": "claude-groq-llama3", "display_name": "x"}]}

    monkeypatch.setattr("gateway.main.get_discovery_payload", fake_payload)
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "claude-groq-llama3"


@pytest.mark.asyncio
async def test_models_free_filter(client, monkeypatch):
    async def fake_payload(_cfg):
        return {"data": [
            {"id": "claude-openrouter-opus-5", "display_name": "x (OpenRouter)"},
            {"id": "claude-groq-llama3", "display_name": "y (Groq · FREE 🆓)"},
        ]}

    monkeypatch.setattr("gateway.main.get_discovery_payload", fake_payload)
    resp = await client.get("/v1/models?free=1")
    ids = [m["id"] for m in resp.json()["data"]]
    assert ids == ["claude-groq-llama3"]


@pytest.mark.asyncio
async def test_messages_missing_model(client):
    resp = await client.post("/v1/messages", json={"messages": []})
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request"


@pytest.mark.asyncio
async def test_messages_invalid_json(client):
    resp = await client.post("/v1/messages", content=b"not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_messages_stream(client):
    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-groq-llama3", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: message_start" in body
    assert "event: message_stop" in body
    assert '"type":"text_delta"' in body


@pytest.mark.asyncio
async def test_messages_non_stream(client):
    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-groq-llama3", "messages": [{"role": "user", "content": "hi"}], "stream": False, "max_tokens": 16},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message"
    assert data["content"] == [{"type": "text", "text": "hi"}]
    assert data["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_messages_unknown_model_stream(client):
    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-nope-x", "messages": []},
    )
    assert resp.status_code == 200
    assert "model_not_found" in resp.text


class _SlowStub:
    """Backend that stalls before its first event (simulates a slow model)."""
    provider_name = "groq"
    model_prefix = "claude-groq-"

    async def handle_request(self, model, body, headers, cwd=None):
        import asyncio
        await asyncio.sleep(0.15)  # silent while "thinking"
        yield SSEEvent(event="message_start", data={"type": "message_start", "message": {"id": "m", "model": model, "usage": {}}})
        yield SSEEvent(event="content_block_delta", data={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}})
        yield SSEEvent(event="message_stop", data={"type": "message_stop"})


@pytest.mark.asyncio
async def test_streaming_emits_keepalive_ping(monkeypatch):
    """A slow upstream must produce ping heartbeats before the first token,
    and the real events must still arrive afterwards (never cancelled)."""
    from types import SimpleNamespace

    monkeypatch.setattr("gateway.main.get_config",
                        lambda: SimpleNamespace(keepalive_interval=0.05))
    app = create_app()
    get_router().initialize([_SlowStub()])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with c.stream("POST", "/v1/messages", json={
            "model": "claude-groq-llama3", "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            body = "".join([chunk async for chunk in resp.aiter_text()])

    assert "event: ping" in body                 # heartbeat fired during the stall
    assert body.index("event: ping") < body.index("text_delta")  # ping before content
    assert '"text":"hi"' in body.replace(" ", "")  # real event still delivered
    assert "message_stop" in body
