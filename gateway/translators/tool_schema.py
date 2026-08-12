"""
Tool-schema translation between Anthropic tools, OpenAI functions and Gemini
function declarations (plus reverse mappings). Handles required fields, type
mapping, enums, arrays and nested objects.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnthropicTool(BaseModel):
    """Anthropic tool definition."""
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


class OpenAIFunction(BaseModel):
    """OpenAI function definition."""
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class GeminiFunctionDeclaration(BaseModel):
    """Gemini function declaration."""
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


# Type mapping tables
ANTHROPIC_TO_OPENAI_TYPE = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
    "any": "object",  # OpenAI doesn't have 'any', use object
}

ANTHROPIC_TO_GEMINI_TYPE = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
    "any": "OBJECT",
}


def _convert_schema(anthropic_schema: dict[str, Any], type_map: dict[str, str]) -> dict[str, Any]:
    """Recursively convert Anthropic JSON Schema to target schema format."""
    if not isinstance(anthropic_schema, dict):
        return {"type": "object", "properties": {}}

    schema_type = anthropic_schema.get("type", "object")
    converted: dict[str, Any] = {"type": type_map.get(schema_type, "object")}

    # Handle properties for objects
    if schema_type == "object" and "properties" in anthropic_schema:
        converted["properties"] = {
            k: _convert_schema(v, type_map) for k, v in anthropic_schema["properties"].items()
        }
        if "required" in anthropic_schema:
            converted["required"] = anthropic_schema["required"]
        if "additionalProperties" in anthropic_schema:
            converted["additionalProperties"] = anthropic_schema["additionalProperties"]

    # Handle array items
    if schema_type == "array" and "items" in anthropic_schema:
        converted["items"] = _convert_schema(anthropic_schema["items"], type_map)

    # Handle enum
    if "enum" in anthropic_schema:
        converted["enum"] = anthropic_schema["enum"]

    # Handle description
    if "description" in anthropic_schema:
        converted["description"] = anthropic_schema["description"]

    # Handle format
    if "format" in anthropic_schema:
        converted["format"] = anthropic_schema["format"]

    # Handle min/max for numbers
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in anthropic_schema:
            converted[key] = anthropic_schema[key]

    return converted


def anthropic_tool_to_openai_function(tool: dict[str, Any]) -> dict[str, Any]:
    """
    Convert Anthropic tool to OpenAI function format.

    Anthropic: {name, description, input_schema: {type, properties, required}}
    OpenAI: {name, description, parameters: {type, properties, required}}
    """
    name = tool.get("name", "")
    description = tool.get("description", "")
    input_schema = tool.get("input_schema", {})

    # Ensure input_schema is an object with properties
    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        input_schema = {"type": "object", "properties": input_schema if isinstance(input_schema, dict) else {}}

    parameters = _convert_schema(input_schema, ANTHROPIC_TO_OPENAI_TYPE)

    return {
        "name": name,
        "description": description,
        "parameters": parameters,
    }


def anthropic_tool_to_gemini_declaration(tool: dict[str, Any]) -> dict[str, Any]:
    """
    Convert Anthropic tool to Gemini functionDeclaration format.

    Gemini uses upper-case type names (STRING, NUMBER, etc.)
    """
    name = tool.get("name", "")
    description = tool.get("description", "")
    input_schema = tool.get("input_schema", {})

    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        input_schema = {"type": "object", "properties": input_schema if isinstance(input_schema, dict) else {}}

    parameters = _convert_schema(input_schema, ANTHROPIC_TO_GEMINI_TYPE)

    # Gemini expects parameters as a Schema object
    return {
        "name": name,
        "description": description or "",
        "parameters": parameters,
    }


def openai_function_to_anthropic_tool(fn: dict[str, Any]) -> dict[str, Any]:
    """Reverse: OpenAI function → Anthropic tool."""
    name = fn.get("name", "")
    description = fn.get("description", "")
    parameters = fn.get("parameters", {})

    # Convert OpenAI types back to Anthropic (lowercase). Exclude the lossy
    # "any" alias so that "object" round-trips to "object", not "any".
    reverse_map = {v: k for k, v in ANTHROPIC_TO_OPENAI_TYPE.items() if k != "any"}
    input_schema = _convert_schema_reverse(parameters, reverse_map)

    return {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }


def _convert_schema_reverse(schema: dict[str, Any], type_map: dict[str, str]) -> dict[str, Any]:
    """Reverse conversion from OpenAI/Gemini schema to Anthropic."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    schema_type = schema.get("type", "object")
    converted: dict[str, Any] = {"type": type_map.get(schema_type, "object")}

    if schema_type == "object" and "properties" in schema:
        converted["properties"] = {
            k: _convert_schema_reverse(v, type_map) for k, v in schema["properties"].items()
        }
        if "required" in schema:
            converted["required"] = schema["required"]

    if schema_type == "array" and "items" in schema:
        converted["items"] = _convert_schema_reverse(schema["items"], type_map)

    if "enum" in schema:
        converted["enum"] = schema["enum"]
    if "description" in schema:
        converted["description"] = schema["description"]
    if "format" in schema:
        converted["format"] = schema["format"]
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in schema:
            converted[key] = schema[key]

    return converted


def batch_convert_tools(
    tools: list[dict[str, Any]],
    target: str = "openai"
) -> list[dict[str, Any]]:
    """
    Batch convert tools to target format.

    Args:
        tools: List of Anthropic tool dicts
        target: "openai" or "gemini"

    Returns:
        List of converted tool dicts
    """
    if target == "openai":
        return [anthropic_tool_to_openai_function(t) for t in tools]
    elif target == "gemini":
        return [anthropic_tool_to_gemini_declaration(t) for t in tools]
    else:
        raise ValueError(f"Unknown target: {target}")


def extract_tool_names(tools: list[dict[str, Any]]) -> list[str]:
    """Extract tool names from tool list."""
    return [t.get("name", "") for t in tools if t.get("name")]
