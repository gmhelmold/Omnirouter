"""
Tests for the opencode backend (session-API bridge).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gateway.backends.opencode_bridge import (
    OpencodeBridgeBackend,
    _messages_to_prompt,
)


class TestMessagesToPrompt:
    def test_single_user_turn_is_verbatim(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert _messages_to_prompt(msgs) == "hello"

    def test_content_blocks_are_flattened(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]}]
        assert _messages_to_prompt(msgs) == "a\nb"

    def test_multi_turn_is_role_tagged(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
            {"role": "user", "content": "name?"},
        ]
        out = _messages_to_prompt(msgs)
        assert "[user]: hi" in out
        assert "[assistant]: yo" in out
        assert "[user]: name?" in out


class TestExtractText:
    def test_concatenates_text_parts_only(self):
        reply = {"parts": [
            {"type": "reasoning", "text": "ignore"},
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]}
        assert OpencodeBridgeBackend._extract_text(reply) == "hello world"

    def test_missing_parts_is_empty(self):
        assert OpencodeBridgeBackend._extract_text({}) == ""


def _events(gen_events):
    return [(e.event, e.data) for e in gen_events]


@pytest.mark.asyncio
@respx.mock
async def test_handle_request_happy_path(mock_config, monkeypatch):
    monkeypatch.setattr("gateway.backends.opencode_bridge.get_config", lambda: mock_config)
    mock_config.opencode_serve_url = "http://oc.test"
    mock_config.opencode_agent = "general"
    mock_config.opencode_model_map = {"big-pickle": "big-pickle"}

    base = "http://oc.test"
    respx.post(f"{base}/session").mock(return_value=httpx.Response(200, json={"id": "ses_1"}))
    msg_route = respx.post(f"{base}/session/ses_1/message").mock(
        return_value=httpx.Response(200, json={"parts": [{"type": "text", "text": "hi there"}]})
    )
    respx.delete(f"{base}/session/ses_1").mock(return_value=httpx.Response(200))

    backend = OpencodeBridgeBackend()
    events = [e async for e in backend.handle_request(
        "claude-opencode-big-pickle",
        {"messages": [{"role": "user", "content": "hey"}]},
        {},
    )]

    names = [e.event for e in events]
    assert names == [
        "message_start", "content_block_start", "content_block_delta",
        "content_block_stop", "message_delta", "message_stop",
    ]
    # text delta carries the reply
    delta = next(e for e in events if e.event == "content_block_delta")
    assert delta.data["delta"]["text"] == "hi there"
    # the correct opencode modelID was sent
    sent = msg_route.calls.last.request
    assert b'"modelID":"big-pickle"' in sent.content.replace(b" ", b"")
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_handle_request_serve_down(mock_config, monkeypatch):
    monkeypatch.setattr("gateway.backends.opencode_bridge.get_config", lambda: mock_config)
    mock_config.opencode_serve_url = "http://oc.test"
    mock_config.opencode_model_map = {}

    respx.post("http://oc.test/session").mock(side_effect=httpx.ConnectError("refused"))

    backend = OpencodeBridgeBackend()
    events = [e async for e in backend.handle_request(
        "claude-opencode-big-pickle",
        {"messages": [{"role": "user", "content": "hey"}]},
        {},
    )]
    assert len(events) == 1
    assert events[0].event == "error"
    assert events[0].data["error"]["type"] == "connection_error"
    await backend.close()
