"""
Tests for OpenAI translator module.
"""

from __future__ import annotations

import pytest

from gateway.translators.openai import (
    anthropic_to_openai,
    openai_sse_to_anthropic,
    OpenAIStreamBuffer,
    SSEEvent,
    build_openai_request,
)


class TestAnthropicToOpenAI:
    def test_simple_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        openai_msgs, functions = anthropic_to_openai(messages, None)

        assert len(openai_msgs) == 3
        assert openai_msgs[0]["role"] == "system"
        assert openai_msgs[1]["role"] == "user"
        assert openai_msgs[2]["role"] == "assistant"

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
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": "file content",
                    }
                ],
            },
        ]
        openai_msgs, _ = anthropic_to_openai(messages, None)

        # Should have system, user, assistant with tool_calls, tool
        assert len(openai_msgs) == 4
        # Check tool result became function role
        tool_msg = openai_msgs[-1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_1"

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
        assert len(functions) == 1
        assert functions[0]["name"] == "read_file"
        assert functions[0]["parameters"]["type"] == "object"


class TestOpenAISSEParsing:
    def test_text_delta(self):
        buffer = OpenAIStreamBuffer()
        line = 'data: {"choices":[{"delta":{"content":"Hello"}}]}'
        event, buffer = openai_sse_to_anthropic(line, buffer)

        assert event is not None
        assert event.event == "content_block_delta"
        assert event.data["delta"]["type"] == "text"
        assert event.data["delta"]["text"] == "Hello"

    def test_tool_call_streaming(self):
        buffer = OpenAIStreamBuffer()

        # First chunk - tool call starts
        line1 = 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read"}}]}}]}'
        event, buffer = openai_sse_to_anthropic(line1, buffer)
        assert event is None  # Buffered

        # Second chunk - function arguments
        line2 = 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":"/tmp\\"}"}}]}}]}'
        event, buffer = openai_sse_to_anthropic(line2, buffer)
        assert event is None  # Still buffered

        # Final chunk - finish
        line3 = 'data: {"choices":[{"finish_reason":"tool_calls"}]}'
        event, buffer = openai_sse_to_anthropic(line3, buffer)
        assert event is not None
        assert event.event == "content_block_delta"
        assert event.data["delta"]["type"] == "tool_use"
        assert event.data["delta"]["name"] == "read"

    def test_done_marker(self):
        buffer = OpenAIStreamBuffer()
        line = "data: [DONE]"
        event, buffer = openai_sse_to_anthropic(line, buffer)

        assert event is not None
        assert event.event == "message_stop"

    def test_message_start(self):
        buffer = OpenAIStreamBuffer()
        line = 'data: {"id":"msg_123","choices":[{"delta":{"role":"assistant"}}]}'
        event, buffer = openai_sse_to_anthropic(line, buffer)

        assert event is not None
        assert event.event == "message_start"
        assert event.data["message"]["id"] == "msg_123"


class TestBuildOpenAIRequest:
    def test_basic_request(self):
        req = build_openai_request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
        )
        assert req["model"] == "gpt-4"
        assert req["stream"] is True
        assert req["messages"][0]["content"] == "Hi"

    def test_with_tools(self):
        functions = [{"name": "test", "parameters": {}}]
        req = build_openai_request(
            model="gpt-4",
            messages=[],
            tools=functions,
            stream=True,
        )
        assert req["tools"][0]["function"]["name"] == "test"
        assert req["tool_choice"] == "auto"

    def test_with_temp_and_tokens(self):
        req = build_openai_request(
            model="gpt-4",
            messages=[],
            temperature=0.5,
            max_tokens=100,
        )
        assert req["temperature"] == 0.5
        assert req["max_tokens"] == 100