"""
OpenAI <-> Anthropic translation.

Covers:
- Request translation (Anthropic messages + system + tools -> OpenAI chat).
- A streaming state machine that turns an OpenAI SSE stream into a *correct*
  Anthropic event sequence: message_start, content_block_start,
  content_block_delta (text_delta / input_json_delta), content_block_stop,
  message_delta, message_stop.
- Error normalization to Anthropic format.

The translator is a stateful object rather than a single-shot function so that
one upstream line can emit several Anthropic events (e.g. opening a block and
its first delta) without ever dropping any.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from gateway.backends.base import SSEEvent
from gateway.translators.tool_schema import anthropic_tool_to_openai_function

# OpenAI finish_reason -> Anthropic stop_reason
_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def _system_to_text(system: Any) -> str | None:
    """Normalize an Anthropic top-level ``system`` field to plain text."""
    if system is None:
        return None
    if isinstance(system, str):
        return system or None
    if isinstance(system, list):
        parts = [b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
        return text or None
    return None


def anthropic_to_openai(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    system: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """
    Convert Anthropic messages + system + tools to OpenAI format.

    Returns ``(openai_messages, openai_functions)``.
    """
    openai_messages: list[dict[str, Any]] = []
    openai_functions: list[dict[str, Any]] | None = None

    if tools:
        openai_functions = [anthropic_tool_to_openai_function(t) for t in tools]

    system_text = _system_to_text(system)
    if system_text:
        openai_messages.append({"role": "system", "content": system_text})

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            text = content if isinstance(content, str) else _system_to_text(content)
            if text:
                openai_messages.append({"role": "system", "content": text})

        elif role == "user":
            if isinstance(content, str):
                openai_messages.append({"role": "user", "content": content})
            elif isinstance(content, list):
                openai_content: list[dict[str, Any]] = []
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        openai_content.append({"type": "text", "text": block.get("text", "")})
                    elif btype == "image":
                        source = block.get("source", {})
                        media_type = source.get("media_type", "image/png")
                        data = source.get("data", "")
                        url = data if data.startswith("http") else f"data:{media_type};base64,{data}"
                        openai_content.append({"type": "image_url", "image_url": {"url": url}})
                    elif btype == "tool_result":
                        tool_use_id = block.get("tool_use_id", "")
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = "\n".join(
                                c.get("text", "") for c in result_content
                                if isinstance(c, dict) and c.get("type") == "text"
                            )
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": str(result_content),
                        })
                if openai_content:
                    openai_messages.append({"role": "user", "content": openai_content})

        elif role == "assistant":
            if isinstance(content, str):
                openai_messages.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for idx, block in enumerate(content):
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", f"call_{idx}"),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
                out: dict[str, Any] = {"role": "assistant"}
                out["content"] = "\n".join(text_parts) if text_parts else None
                if tool_calls:
                    out["tool_calls"] = tool_calls
                openai_messages.append(out)

    return openai_messages, openai_functions


def build_openai_request(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    stream: bool = True,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
) -> dict[str, Any]:
    """Build an OpenAI chat completions request body."""
    req: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        req["tools"] = [{"type": "function", "function": f} for f in tools]
        req["tool_choice"] = "auto"
    if temperature is not None:
        req["temperature"] = temperature
    if top_p is not None:
        req["top_p"] = top_p
    if max_tokens is not None:
        req["max_tokens"] = max_tokens
    if stop:
        req["stop"] = stop
    return req


class OpenAITranslator:
    """Streaming state machine: OpenAI SSE lines -> Anthropic SSE events."""

    def __init__(self) -> None:
        self._message_started = False
        self._finished = False
        self._next_index = 0
        self._text_index: int | None = None  # anthropic index of open text block
        # openai tool index -> record
        self._tools: dict[int, dict[str, Any]] = {}
        self._stop_reason = "end_turn"
        self._output_tokens = 0
        self._model = ""

    # -- public API ---------------------------------------------------------

    def feed(self, line: str) -> list[SSEEvent]:
        """Consume one raw upstream SSE line, return Anthropic events."""
        line = line.strip()
        if not line or not line.startswith("data:"):
            return []

        data_str = line[5:].strip()
        if data_str == "[DONE]":
            return self.flush()

        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return []

        events: list[SSEEvent] = []

        if not self._model:
            self._model = chunk.get("model", "") or ""

        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
            self._output_tokens = usage["completion_tokens"]

        choices = chunk.get("choices") or []
        if not choices:
            return events

        choice = choices[0]
        delta = choice.get("delta", {}) or {}
        finish_reason = choice.get("finish_reason")

        events.extend(self._ensure_started(chunk))

        content = delta.get("content")
        if content:
            events.extend(self._emit_text(content))

        tool_calls = delta.get("tool_calls")
        if tool_calls:
            events.extend(self._emit_tool_calls(tool_calls))

        if finish_reason is not None:
            self._stop_reason = _STOP_REASON_MAP.get(finish_reason, "end_turn")
            events.extend(self.flush())

        return events

    def flush(self) -> list[SSEEvent]:
        """Emit closing events. Idempotent."""
        if self._finished:
            return []
        self._finished = True

        events: list[SSEEvent] = []
        if not self._message_started:
            # Nothing ever streamed; still produce a well-formed empty message.
            events.extend(self._ensure_started({}))

        if self._text_index is not None:
            events.append(self._content_block_stop(self._text_index))
            self._text_index = None

        for rec in self._tools.values():
            if rec["started"]:
                events.append(self._content_block_stop(rec["anthropic_index"]))

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

    # -- internals ----------------------------------------------------------

    def _ensure_started(self, chunk: dict[str, Any]) -> list[SSEEvent]:
        if self._message_started:
            return []
        self._message_started = True
        return [SSEEvent(
            event="message_start",
            data={
                "type": "message_start",
                "message": {
                    "id": chunk.get("id") or "msg_gateway",
                    "type": "message",
                    "role": "assistant",
                    "model": self._model,
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

    def _emit_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[SSEEvent]:
        events: list[SSEEvent] = []

        # A tool call closes any open text block.
        if self._text_index is not None:
            events.append(self._content_block_stop(self._text_index))
            self._text_index = None

        for tc in tool_calls:
            key = tc.get("index", 0)
            rec = self._tools.get(key)
            if rec is None:
                rec = {"id": "", "name": "", "started": False, "anthropic_index": None, "pending_args": ""}
                self._tools[key] = rec

            if tc.get("id"):
                rec["id"] = tc["id"]
            func = tc.get("function") or {}
            if func.get("name"):
                rec["name"] += func["name"]
            arg_fragment = func.get("arguments") or ""

            if not rec["started"] and rec["name"]:
                rec["started"] = True
                rec["anthropic_index"] = self._next_index
                self._next_index += 1
                events.append(SSEEvent(
                    event="content_block_start",
                    data={
                        "type": "content_block_start",
                        "index": rec["anthropic_index"],
                        "content_block": {
                            "type": "tool_use",
                            "id": rec["id"] or f"toolu_{key}",
                            "name": rec["name"],
                            "input": {},
                        },
                    },
                ))
                # Flush any args that arrived before the name.
                if rec["pending_args"]:
                    arg_fragment = rec["pending_args"] + arg_fragment
                    rec["pending_args"] = ""

            if arg_fragment:
                if rec["started"]:
                    events.append(SSEEvent(
                        event="content_block_delta",
                        data={
                            "type": "content_block_delta",
                            "index": rec["anthropic_index"],
                            "delta": {"type": "input_json_delta", "partial_json": arg_fragment},
                        },
                    ))
                else:
                    rec["pending_args"] += arg_fragment

        return events

    @staticmethod
    def _content_block_stop(index: int) -> SSEEvent:
        return SSEEvent(
            event="content_block_stop",
            data={"type": "content_block_stop", "index": index},
        )


async def stream_openai_compatible(
    client: httpx.AsyncClient,
    url: str,
    request_body: dict[str, Any],
    headers: dict[str, str],
    *,
    provider: str = "provider",
    read_timeout: float = 600.0,
) -> AsyncGenerator[SSEEvent, None]:
    """
    POST an OpenAI-compatible chat completions request and stream translated
    Anthropic events. Shared by the Groq / Mistral / NIM / opencode backends.
    """
    translator = OpenAITranslator()
    timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=60.0, pool=10.0)
    try:
        async with client.stream("POST", url, json=request_body, headers=headers, timeout=timeout) as response:
            if response.status_code >= 400:
                await response.aread()
                yield SSEEvent(event="error", data=normalize_openai_error(response))
                return
            async for line in response.aiter_lines():
                for event in translator.feed(line):
                    yield event
        for event in translator.flush():
            yield event
    except httpx.TimeoutException:
        yield SSEEvent(
            event="error",
            data={"type": "error", "error": {"type": "timeout", "message": f"{provider} request timed out"}},
        )
    except httpx.HTTPError as exc:
        yield SSEEvent(
            event="error",
            data={"type": "error", "error": {"type": "connection_error", "message": str(exc)}},
        )


def normalize_openai_error(response: httpx.Response) -> dict[str, Any]:
    """Convert an OpenAI error response to Anthropic error format."""
    try:
        err_data = response.json()
        openai_err = err_data.get("error", {})
        etype = openai_err.get("type") or "api_error"
        if response.status_code == 429:
            etype = "rate_limit"
        return {
            "type": "error",
            "error": {
                "type": etype,
                "message": openai_err.get("message", "Unknown error from provider"),
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
