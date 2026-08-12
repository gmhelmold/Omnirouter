"""
Gemini <-> Anthropic translation.

Mirrors the OpenAI translator: a request converter plus a streaming state
machine that turns a Gemini ``streamGenerateContent?alt=sse`` stream into a
correct Anthropic event sequence. Gemini emits complete ``functionCall``
objects (not partial argument fragments), so each tool call becomes a single
``input_json_delta`` carrying the full JSON.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from gateway.backends.base import SSEEvent
from gateway.translators.tool_schema import anthropic_tool_to_gemini_declaration

_FINISH_REASON_MAP = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "end_turn",
    "RECITATION": "end_turn",
    "OTHER": "end_turn",
}


def _system_to_text(system: Any) -> str | None:
    if system is None:
        return None
    if isinstance(system, str):
        return system or None
    if isinstance(system, list):
        parts = [b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
        return text or None
    return None


def anthropic_to_gemini(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    system: Any = None,
    generation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert Anthropic messages + system + tools to a Gemini request body."""
    gemini_contents: list[dict[str, Any]] = []
    system_instruction = None

    system_text = _system_to_text(system)
    if system_text:
        system_instruction = {"parts": [{"text": system_text}]}

    gemini_tools = None
    if tools:
        gemini_tools = [{
            "functionDeclarations": [anthropic_tool_to_gemini_declaration(t) for t in tools]
        }]

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            text = content if isinstance(content, str) else _system_to_text(content)
            if text:
                system_instruction = {"parts": [{"text": text}]}

        elif role == "user":
            parts: list[dict[str, Any]] = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        parts.append({"text": block.get("text", "")})
                    elif btype == "image":
                        source = block.get("source", {})
                        parts.append({
                            "inline_data": {
                                "mime_type": source.get("media_type", "image/png"),
                                "data": source.get("data", ""),
                            }
                        })
                    elif btype == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = "\n".join(
                                c.get("text", "") for c in result_content
                                if isinstance(c, dict) and c.get("type") == "text"
                            )
                        parts.append({
                            "functionResponse": {
                                "name": block.get("tool_use_id", ""),
                                "response": {"content": str(result_content)},
                            }
                        })
            if parts:
                gemini_contents.append({"role": "user", "parts": parts})

        elif role == "assistant":
            parts = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        parts.append({"text": block.get("text", "")})
                    elif btype == "tool_use":
                        parts.append({
                            "functionCall": {
                                "name": block.get("name", ""),
                                "args": block.get("input", {}),
                            }
                        })
            if parts:
                gemini_contents.append({"role": "model", "parts": parts})

    request: dict[str, Any] = {"contents": gemini_contents}
    if generation_config:
        request["generationConfig"] = generation_config
    if system_instruction:
        request["systemInstruction"] = system_instruction
    if gemini_tools:
        request["tools"] = gemini_tools
    return request


def build_generation_config(
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if temperature is not None:
        cfg["temperature"] = temperature
    if top_p is not None:
        cfg["topP"] = top_p
    if max_tokens is not None:
        cfg["maxOutputTokens"] = max_tokens
    if stop:
        cfg["stopSequences"] = stop
    return cfg


class GeminiTranslator:
    """Streaming state machine: Gemini SSE lines -> Anthropic SSE events."""

    def __init__(self) -> None:
        self._message_started = False
        self._finished = False
        self._next_index = 0
        self._text_index: int | None = None
        self._open_tool_indices: list[int] = []
        self._stop_reason = "end_turn"
        self._output_tokens = 0

    def feed(self, line: str) -> list[SSEEvent]:
        line = line.strip()
        if not line or not line.startswith("data:"):
            return []
        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            return self.flush() if data_str == "[DONE]" else []

        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return []

        events: list[SSEEvent] = []

        usage = chunk.get("usageMetadata")
        if isinstance(usage, dict) and usage.get("candidatesTokenCount") is not None:
            self._output_tokens = usage["candidatesTokenCount"]

        candidates = chunk.get("candidates") or []
        if not candidates:
            return events

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        finish_reason = candidate.get("finishReason")

        for part in parts:
            if part.get("text"):
                events.extend(self._ensure_started())
                events.extend(self._emit_text(part["text"]))
            elif "functionCall" in part:
                events.extend(self._ensure_started())
                events.extend(self._emit_function_call(part["functionCall"]))

        if finish_reason is not None:
            self._stop_reason = _FINISH_REASON_MAP.get(finish_reason, "end_turn")
            if self._open_tool_indices:
                self._stop_reason = "tool_use"
            events.extend(self.flush())

        return events

    def flush(self) -> list[SSEEvent]:
        if self._finished:
            return []
        self._finished = True

        events: list[SSEEvent] = []
        if not self._message_started:
            events.extend(self._ensure_started())

        if self._text_index is not None:
            events.append(self._content_block_stop(self._text_index))
            self._text_index = None
        for idx in self._open_tool_indices:
            events.append(self._content_block_stop(idx))
        self._open_tool_indices = []

        events.append(SSEEvent(
            event="message_delta",
            data={
                "type": "message_delta",
                "delta": {"stop_reason": self._stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": self._output_tokens},
            },
        ))
        events.append(SSEEvent(event="message_stop", data={"type": "message_stop"}))
        return events

    def _ensure_started(self) -> list[SSEEvent]:
        if self._message_started:
            return []
        self._message_started = True
        return [SSEEvent(
            event="message_start",
            data={
                "type": "message_start",
                "message": {
                    "id": "msg_gateway",
                    "type": "message",
                    "role": "assistant",
                    "model": "gemini",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )]

    def _emit_text(self, text: str) -> list[SSEEvent]:
        events: list[SSEEvent] = []
        if self._text_index is None:
            self._text_index = self._next_index
            self._next_index += 1
            events.append(SSEEvent(
                event="content_block_start",
                data={
                    "type": "content_block_start",
                    "index": self._text_index,
                    "content_block": {"type": "text", "text": ""},
                },
            ))
        events.append(SSEEvent(
            event="content_block_delta",
            data={
                "type": "content_block_delta",
                "index": self._text_index,
                "delta": {"type": "text_delta", "text": text},
            },
        ))
        return events

    def _emit_function_call(self, fc: dict[str, Any]) -> list[SSEEvent]:
        events: list[SSEEvent] = []
        if self._text_index is not None:
            events.append(self._content_block_stop(self._text_index))
            self._text_index = None

        index = self._next_index
        self._next_index += 1
        self._open_tool_indices.append(index)
        name = fc.get("name", "")
        args = fc.get("args", {}) or {}
        events.append(SSEEvent(
            event="content_block_start",
            data={
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": f"toolu_{name}_{index}",
                    "name": name,
                    "input": {},
                },
            },
        ))
        events.append(SSEEvent(
            event="content_block_delta",
            data={
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)},
            },
        ))
        return events

    @staticmethod
    def _content_block_stop(index: int) -> SSEEvent:
        return SSEEvent(
            event="content_block_stop",
            data={"type": "content_block_stop", "index": index},
        )


def normalize_gemini_error(response: httpx.Response) -> dict[str, Any]:
    """Convert a Gemini error response to Anthropic error format."""
    try:
        err_data = response.json()
        gemini_err = err_data.get("error", {})
        return {
            "type": "error",
            "error": {
                "type": "rate_limit" if response.status_code == 429 else "api_error",
                "message": gemini_err.get("message", "Unknown error from Gemini"),
            },
        }
    except Exception:
        return {
            "type": "error",
            "error": {
                "type": "rate_limit" if response.status_code == 429 else "api_error",
                "message": f"HTTP {response.status_code}: {response.text[:500]}",
            },
        }
