"""
Tests for non-streaming message reconstruction.
"""

from __future__ import annotations

from gateway.backends.base import SSEEvent
from gateway.messages import collect_message
from gateway.translators.openai import OpenAITranslator


def test_collect_text_message():
    events = []
    t = OpenAITranslator()
    for line in [
        'data: {"id":"msg_9","model":"llama","choices":[{"delta":{"role":"assistant","content":"Hello "}}]}',
        'data: {"choices":[{"delta":{"content":"world"}}]}',
        'data: {"choices":[{"finish_reason":"stop"}],"usage":{"completion_tokens":3}}',
    ]:
        events.extend(t.feed(line))

    msg = collect_message(events, "claude-groq-llama3")
    assert msg["role"] == "assistant"
    assert msg["stop_reason"] == "end_turn"
    assert msg["content"] == [{"type": "text", "text": "Hello world"}]
    assert msg["usage"]["output_tokens"] == 3


def test_collect_tool_use_message():
    events = []
    t = OpenAITranslator()
    for line in [
        'data: {"id":"m","choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read","arguments":"{\\"path\\":\\"/tmp\\"}"}}]}}]}',
        'data: {"choices":[{"finish_reason":"tool_calls"}]}',
    ]:
        events.extend(t.feed(line))

    msg = collect_message(events, "claude-groq-llama3")
    assert msg["stop_reason"] == "tool_use"
    block = msg["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "read"
    assert block["input"] == {"path": "/tmp"}
    assert "_json" not in block


def test_collect_handles_bad_tool_json():
    events = [
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "c1", "name": "x", "input": {}},
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "{not json"},
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
    ]
    msg = collect_message(events, "m")
    assert msg["content"][0]["input"] == {}
