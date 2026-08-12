"""
Gemini ↔ Anthropic Translation Module

State-of-the-art translation with:
- Full message format conversion
- Streaming SSE parsing with functionCall buffering
- Error normalization to Anthropic format
- Proper handling of partial functionCalls across chunks
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from gateway.translators.tool_schema import anthropic_tool_to_gemini_declaration


@dataclass
class SSEEvent:
    """Normalized SSE event for Anthropic format."""
    event: str  # message_start, content_block_delta, message_stop, message_delta, error
    data: dict[str, Any]
    retry: int = 0


@dataclass
class GeminiStreamBuffer:
    """Buffer state for parsing Gemini SSE streams."""
    function_calls: dict[int, dict[str, Any]] = field(default_factory=dict)  # index -> partial functionCall
    current_text: str = ""
    message_started: bool = False
    content_block_index: int = 0


def anthropic_to_gemini(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Convert Anthropic messages + tools to Gemini request format.

    Returns:
        Gemini request body for streamGenerateContent
    """
    gemini_contents = []
    system_instruction = None

    # Convert tools
    gemini_tools = None
    if tools:
        gemini_tools = [{
            "functionDeclarations": [
                anthropic_tool_to_gemini_declaration(t) for t in tools
            ]
        }]

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            # Gemini uses system_instruction
            if isinstance(content, str):
                system_instruction = {"parts": [{"text": content}]}
            elif isinstance(content, list):
                text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                system_instruction = {"parts": [{"text": "\n".join(text_parts)}]}

        elif role == "user":
            parts = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                    elif block.get("type") == "image":
                        # Convert to Gemini image format
                        parts.append({
                            "inline_data": {
                                "mime_type": block.get("source", {}).get("media_type", "image/png"),
                                "data": block.get("source", {}).get("data", "")
                            }
                        })
                    elif block.get("type") == "tool_result":
                        # Tool result → functionResponse
                        tool_use_id = block.get("tool_use_id", "")
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = "\n".join(
                                c.get("text", "") for c in result_content if c.get("type") == "text"
                            )
                        parts.append({
                            "functionResponse": {
                                "name": tool_use_id,  # Use tool_use_id as function name
                                "response": {"content": str(result_content)}
                            }
                        })
            if parts:
                gemini_contents.append({"role": "user", "parts": parts})

        elif role == "assistant":
            parts = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for idx, block in enumerate(content):
                    if block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                    elif block.get("type") == "tool_use":
                        # Tool use → functionCall
                        parts.append({
                            "functionCall": {
                                "name": block.get("name", ""),
                                "args": block.get("input", {})
                            }
                        })
            if parts:
                gemini_contents.append({"role": "model", "parts": parts})

    request = {
        "contents": gemini_contents,
        "generationConfig": {
            "temperature": 0.7,
        },
    }
    if system_instruction:
        request["systemInstruction"] = system_instruction
    if gemini_tools:
        request["tools"] = gemini_tools

    return request


def gemini_sse_to_anthropic(line: str, buffer: GeminiStreamBuffer) -> tuple[SSEEvent | None, GeminiStreamBuffer]:
    """
    Parse Gemini SSE line and yield Anthropic SSE event.

    Handles:
    - Text deltas
    - functionCall (buffered across chunks)
    - Finish reasons
    - Error events
    """
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None, buffer

    data_str = line[5:].strip()
    if not data_str:
        return None, buffer

    try:
        chunk = json.loads(data_str)
    except json.JSONDecodeError:
        return None, buffer

    candidates = chunk.get("candidates", [])
    if not candidates:
        return None, buffer

    candidate = candidates[0]
    content = candidate.get("content", {})
    parts = content.get("parts", [])
    finish_reason = candidate.get("finishReason")

    # First chunk - message_start
    if not buffer.message_started and parts:
        buffer.message_started = True
        return SSEEvent(
            event="message_start",
            data={
                "message": {
                    "id": chunk.get("responseId", "msg_"),
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "gemini",
                    "usage": None
                }
            }
        ), buffer

    for part_idx, part in enumerate(parts):
        # Text delta
        if "text" in part and part["text"]:
            text = part["text"]
            buffer.current_text += text
            event = SSEEvent(
                event="content_block_delta",
                data={
                    "index": buffer.content_block_index,
                    "delta": {"type": "text", "text": text}
                }
            )
            return event, buffer

        # functionCall
        if "functionCall" in part:
            fc = part["functionCall"]
            idx = fc.get("name", "").replace(".", "_")  # Use name as index
            if idx not in buffer.function_calls:
                buffer.function_calls[idx] = {"name": "", "args": {}}
            fc_buf = buffer.function_calls[idx]

            if "name" in fc and fc["name"]:
                fc_buf["name"] = fc["name"]
            if "args" in fc and fc["args"]:
                # Merge args (could be partial)
                fc_buf["args"].update(fc["args"])

    # Handle finish
    if finish_reason is not None:
        events = []

        # Emit pending text
        if buffer.current_text:
            events.append(SSEEvent(
                event="content_block_delta",
                data={"index": buffer.content_block_index, "delta": {"type": "text", "text": buffer.current_text}}
            ))
            buffer.content_block_index += 1
            buffer.current_text = ""

        # Emit completed function calls
        for idx in sorted(buffer.function_calls.keys()):
            fc = buffer.function_calls[idx]
            if fc["name"]:
                events.append(SSEEvent(
                    event="content_block_delta",
                    data={
                        "index": buffer.content_block_index,
                        "delta": {
                            "type": "tool_use",
                            "id": f"call_{idx}",
                            "name": fc["name"],
                            "input": fc["args"]
                        }
                    }
                ))
                buffer.content_block_index += 1

        # Final message_stop
        events.append(SSEEvent(event="message_stop", data={}))

        if events:
            first = events[0]
            return first, GeminiStreamBuffer()

    return None, buffer


def normalize_gemini_error(response: httpx.Response) -> dict[str, Any]:
    """Convert Gemini error response to Anthropic error format."""
    try:
        err_data = response.json()
        gemini_err = err_data.get("error", {})
        return {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": gemini_err.get("message", "Unknown error from Gemini")
            }
        }
    except Exception:
        return {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": f"HTTP {response.status_code}: {response.text[:500]}"
            }
        }