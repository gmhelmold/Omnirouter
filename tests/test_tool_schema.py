"""
Tests for tool schema translation module.
"""

from __future__ import annotations

import pytest

from gateway.translators.tool_schema import (
    anthropic_tool_to_gemini_declaration,
    anthropic_tool_to_openai_function,
    batch_convert_tools,
    openai_function_to_anthropic_tool,
)


class TestAnthropicToOpenAI:
    def test_basic_tool(self):
        tool = {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        }
        result = anthropic_tool_to_openai_function(tool)

        assert result["name"] == "read_file"
        assert result["description"] == "Read a file"
        assert result["parameters"]["type"] == "object"
        assert "path" in result["parameters"]["properties"]
        assert result["parameters"]["required"] == ["path"]

    def test_tool_with_enum(self):
        tool = {
            "name": "choose",
            "input_schema": {
                "type": "object",
                "properties": {
                    "option": {"type": "string", "enum": ["a", "b", "c"]},
                },
            },
        }
        result = anthropic_tool_to_openai_function(tool)
        assert result["parameters"]["properties"]["option"]["enum"] == ["a", "b", "c"]

    def test_tool_with_array(self):
        tool = {
            "name": "multi",
            "input_schema": {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
        result = anthropic_tool_to_openai_function(tool)
        assert result["parameters"]["properties"]["items"]["type"] == "array"
        assert result["parameters"]["properties"]["items"]["items"]["type"] == "string"

    def test_missing_schema(self):
        tool = {"name": "simple", "description": "No schema"}
        result = anthropic_tool_to_openai_function(tool)
        assert result["parameters"]["type"] == "object"
        assert result["parameters"]["properties"] == {}


class TestAnthropicToGemini:
    def test_basic_tool(self):
        tool = {
            "name": "read_file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
        result = anthropic_tool_to_gemini_declaration(tool)

        assert result["name"] == "read_file"
        assert result["parameters"]["type"] == "OBJECT"
        assert "path" in result["parameters"]["properties"]
        assert result["parameters"]["properties"]["path"]["type"] == "STRING"

    def test_uppercase_types(self):
        tool = {
            "name": "test",
            "input_schema": {
                "type": "object",
                "properties": {
                    "num": {"type": "number"},
                    "int": {"type": "integer"},
                    "bool": {"type": "boolean"},
                },
            },
        }
        result = anthropic_tool_to_gemini_declaration(tool)
        props = result["parameters"]["properties"]
        assert props["num"]["type"] == "NUMBER"
        assert props["int"]["type"] == "INTEGER"
        assert props["bool"]["type"] == "BOOLEAN"


class TestReverseMapping:
    def test_openai_to_anthropic(self):
        fn = {
            "name": "test",
            "description": "Test function",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        }
        result = openai_function_to_anthropic_tool(fn)
        assert result["name"] == "test"
        assert result["input_schema"]["type"] == "object"
        assert result["input_schema"]["properties"]["x"]["type"] == "string"


class TestBatchConvert:
    def test_batch_openai(self):
        tools = [
            {"name": "a", "input_schema": {"type": "object"}},
            {"name": "b", "input_schema": {"type": "object"}},
        ]
        result = batch_convert_tools(tools, "openai")
        assert len(result) == 2
        assert result[0]["name"] == "a"
        assert result[1]["name"] == "b"

    def test_batch_gemini(self):
        tools = [{"name": "a", "input_schema": {"type": "object"}}]
        result = batch_convert_tools(tools, "gemini")
        assert result[0]["parameters"]["type"] == "OBJECT"

    def test_invalid_target(self):
        with pytest.raises(ValueError):
            batch_convert_tools([], "invalid")
