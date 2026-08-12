"""
Tests for the OpenCode Zen backend (direct, OpenAI-compatible, tool-capable).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gateway.backends.opencode_bridge import OpencodeBridgeBackend


def _sse(chunks: list[dict]) -> str:
    import orjson
    lines = [f"data: {orjson.dumps(c).decode()}" for c in chunks]
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


@pytest.mark.asyncio
@respx.mock
async def test_maps_key_and_forwards_tools(mock_config, monkeypatch):
    monkeypatch.setattr("gateway.backends.opencode_bridge.get_config", lambda: mock_config)
    mock_config.opencode_base_url = "https://opencode.ai/zen/v1"
    mock_config.opencode_api_key = "public"
    mock_config.opencode_model_map = {"big-pickle": "big-pickle"}

    route = respx.post("https://opencode.ai/zen/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=_sse([
            {"choices": [{"delta": {"content": "hi"}, "index": 0}]},
            {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]},
        ]))
    )

    backend = OpencodeBridgeBackend()
    body = {
        "messages": [{"role": "user", "content": "hey"}],
        "tools": [{"name": "get_weather", "description": "w",
                   "input_schema": {"type": "object", "properties": {}}}],
    }
    events = [e async for e in backend.handle_request("claude-opencode-big-pickle", body, {})]

    # Streamed text translated into Anthropic events.
    assert any(e.event == "content_block_delta" for e in events)
    assert any(e.event == "message_stop" for e in events)

    # Upstream request: friendly key mapped to Zen modelID, tools forwarded,
    # public bearer set.
    req = route.calls.last.request
    sent = req.content.replace(b" ", b"")
    assert b'"model":"big-pickle"' in sent
    assert b'get_weather' in req.content  # tool schema forwarded to the model
    assert req.headers["authorization"] == "Bearer public"
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_upstream_error_is_surfaced(mock_config, monkeypatch):
    monkeypatch.setattr("gateway.backends.opencode_bridge.get_config", lambda: mock_config)
    mock_config.opencode_base_url = "https://opencode.ai/zen/v1"
    mock_config.opencode_api_key = "public"
    mock_config.opencode_model_map = {}

    respx.post("https://opencode.ai/zen/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={
            "error": {"type": "FreeUsageLimitError", "message": "Rate limit exceeded"}
        })
    )

    backend = OpencodeBridgeBackend()
    events = [e async for e in backend.handle_request(
        "claude-opencode-big-pickle",
        {"messages": [{"role": "user", "content": "hey"}]}, {},
    )]
    assert any(e.event == "error" for e in events)
    await backend.close()
