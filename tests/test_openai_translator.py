"""
Tests for the OpenAI <-> Anthropic translator.
"""

from __future__ import annotations

from gateway.translators.openai import (
    OpenAITranslator,
    anthropic_to_openai,
    build_openai_request,
)


def _events(translator: OpenAITranslator, lines: list[str]) -> list:
    out = []
    for line in lines:
        out.extend(translator.feed(line))
    return out


class TestAnthropicToOpenAI:
    def test_simple_messages(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        openai_msgs, functions = anthropic_to_openai(messages, None, "You are helpful")

        assert functions is None
        assert openai_msgs[0] == {"role": "system", "content": "You are helpful"}
        assert openai_msgs[1]["role"] == "user"
        assert openai_msgs[2]["role"] == "assistant"

    def test_system_list_blocks(self):
        system = [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]
        openai_msgs, _ = anthropic_to_openai([], None, system)
        assert openai_msgs[0]["content"] == "line1\nline2"

    def test_tool_result_conversion(self):
        messages = [
            {"role": "user", "content": "Run tool"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "/tmp"}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "file content"}
                ],
            },
        ]
        openai_msgs, _ = anthropic_to_openai(messages, None)

        assert openai_msgs[0]["role"] == "user"
        assert openai_msgs[1]["role"] == "assistant"
        assert openai_msgs[1]["tool_calls"][0]["function"]["name"] == "read"
        tool_msg = openai_msgs[-1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == "file content"

    def test_tools_conversion(self):
        tools = [
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ]
        _, functions = anthropic_to_openai([], tools)
        assert functions is not None
        assert functions[0]["name"] == "read_file"
        assert functions[0]["parameters"]["type"] == "object"


class TestOpenAIStreaming:
    def test_text_stream_sequence(self):
        t = OpenAITranslator()
        events = _events(t, [
            'data: {"id":"msg_1","model":"llama","choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
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
        # text deltas use text_delta with correct index
        deltas = [e for e in events if e.event == "content_block_delta"]
        assert deltas[0].data["delta"] == {"type": "text_delta", "text": "Hel"}
        assert deltas[0].data["index"] == 0
        # stop reason mapped
        md = next(e for e in events if e.event == "message_delta")
        assert md.data["delta"]["stop_reason"] == "end_turn"

    def test_all_data_payloads_have_type(self):
        t = OpenAITranslator()
        events = _events(t, [
            'data: {"id":"m","choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"content":"x"}}]}',
            'data: {"choices":[{"finish_reason":"stop"}]}',
        ])
        for e in events:
            assert e.data.get("type") == e.event

    def test_tool_call_stream(self):
        t = OpenAITranslator()
        events = _events(t, [
            'data: {"id":"m","choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":\\"/tmp\\"}"}}]}}]}',
            'data: {"choices":[{"finish_reason":"tool_calls"}]}',
        ])
        names = [e.event for e in events]
        assert names == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]
        start = next(e for e in events if e.event == "content_block_start")
        assert start.data["content_block"]["type"] == "tool_use"
        assert start.data["content_block"]["name"] == "read"
        assert start.data["content_block"]["id"] == "call_1"
        delta = next(e for e in events if e.event == "content_block_delta")
        assert delta.data["delta"]["type"] == "input_json_delta"
        assert delta.data["delta"]["partial_json"] == '{"path":"/tmp"}'
        md = next(e for e in events if e.event == "message_delta")
        assert md.data["delta"]["stop_reason"] == "tool_use"

    def test_text_then_tool_two_blocks(self):
        t = OpenAITranslator()
        events = _events(t, [
            'data: {"id":"m","choices":[{"delta":{"role":"assistant","content":"thinking"}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"go","arguments":"{}"}}]}}]}',
            'data: {"choices":[{"finish_reason":"tool_calls"}]}',
        ])
        starts = [e for e in events if e.event == "content_block_start"]
        stops = [e for e in events if e.event == "content_block_stop"]
        assert len(starts) == 2
        assert starts[0].data["content_block"]["type"] == "text"
        assert starts[0].data["index"] == 0
        assert starts[1].data["content_block"]["type"] == "tool_use"
        assert starts[1].data["index"] == 1
        assert len(stops) == 2

    def test_done_marker_flushes(self):
        t = OpenAITranslator()
        events = _events(t, [
            'data: {"id":"m","choices":[{"delta":{"role":"assistant","content":"hi"}}]}',
            "data: [DONE]",
        ])
        names = [e.event for e in events]
        assert names[-1] == "message_stop"
        assert "message_delta" in names
        assert names.count("message_stop") == 1

    def test_usage_captured(self):
        t = OpenAITranslator()
        events = _events(t, [
            'data: {"id":"m","choices":[{"delta":{"role":"assistant","content":"hi"}}]}',
            'data: {"choices":[{"finish_reason":"stop"}],"usage":{"completion_tokens":7}}',
        ])
        md = next(e for e in events if e.event == "message_delta")
        assert md.data["usage"]["output_tokens"] == 7


class TestBuildOpenAIRequest:
    def test_basic_request(self):
        req = build_openai_request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])
        assert req["model"] == "gpt-4"
        assert req["stream"] is True

    def test_with_tools(self):
        req = build_openai_request(model="gpt-4", messages=[], tools=[{"name": "t", "parameters": {}}])
        assert req["tools"][0]["function"]["name"] == "t"
        assert req["tool_choice"] == "auto"

    def test_forwards_params(self):
        req = build_openai_request(
            model="gpt-4", messages=[], temperature=0.5, top_p=0.9, max_tokens=100, stop=["x"]
        )
        assert req["temperature"] == 0.5
        assert req["top_p"] == 0.9
        assert req["max_tokens"] == 100
        assert req["stop"] == ["x"]
