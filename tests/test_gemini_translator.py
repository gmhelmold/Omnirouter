"""
Tests for the Gemini <-> Anthropic translator.
"""

from __future__ import annotations

import json

from gateway.translators.gemini import (
    GeminiTranslator,
    anthropic_to_gemini,
    build_generation_config,
)


def _events(t: GeminiTranslator, lines: list[str]) -> list:
    out = []
    for line in lines:
        out.extend(t.feed(line))
    return out


class TestAnthropicToGemini:
    def test_system_instruction(self):
        req = anthropic_to_gemini([{"role": "user", "content": "hi"}], None, "be nice")
        assert req["systemInstruction"]["parts"][0]["text"] == "be nice"
        assert req["contents"][0]["role"] == "user"

    def test_assistant_role_mapped_to_model(self):
        req = anthropic_to_gemini([{"role": "assistant", "content": "ok"}])
        assert req["contents"][0]["role"] == "model"

    def test_tool_use_and_result(self):
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "c1", "name": "read", "input": {"path": "/x"}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "read", "content": "data"}
            ]},
        ]
        req = anthropic_to_gemini(messages)
        assert req["contents"][0]["parts"][0]["functionCall"]["name"] == "read"
        assert req["contents"][1]["parts"][0]["functionResponse"]["name"] == "read"

    def test_generation_config(self):
        cfg = build_generation_config(temperature=0.3, top_p=0.8, max_tokens=64, stop=["END"])
        assert cfg == {"temperature": 0.3, "topP": 0.8, "maxOutputTokens": 64, "stopSequences": ["END"]}


class TestGeminiStreaming:
    def test_text_stream(self):
        t = GeminiTranslator()
        events = _events(t, [
            'data: {"candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}',
            'data: {"candidates":[{"content":{"parts":[{"text":"lo"}]}}]}',
            'data: {"candidates":[{"content":{"parts":[]},"finishReason":"STOP"}],"usageMetadata":{"candidatesTokenCount":4}}',
        ])
        names = [e.event for e in events]
        assert names == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]
        md = next(e for e in events if e.event == "message_delta")
        assert md.data["delta"]["stop_reason"] == "end_turn"
        assert md.data["usage"]["output_tokens"] == 4

    def test_all_payloads_typed(self):
        t = GeminiTranslator()
        events = _events(t, [
            'data: {"candidates":[{"content":{"parts":[{"text":"x"}]},"finishReason":"STOP"}]}',
        ])
        for e in events:
            assert e.data.get("type") == e.event

    def test_function_call(self):
        t = GeminiTranslator()
        events = _events(t, [
            'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"read","args":{"path":"/tmp"}}}]},"finishReason":"STOP"}]}',
        ])
        start = next(e for e in events if e.event == "content_block_start")
        assert start.data["content_block"]["type"] == "tool_use"
        assert start.data["content_block"]["name"] == "read"
        delta = next(e for e in events if e.event == "content_block_delta")
        assert delta.data["delta"]["type"] == "input_json_delta"
        assert json.loads(delta.data["delta"]["partial_json"]) == {"path": "/tmp"}
        md = next(e for e in events if e.event == "message_delta")
        assert md.data["delta"]["stop_reason"] == "tool_use"
