"""
Reconstruct a non-streaming Anthropic Messages response from the SSE event
stream produced by the backends. Used when a client sends ``stream: false``.
"""

from __future__ import annotations

import json
from typing import Any

from gateway.backends.base import SSEEvent


def collect_message(events: list[SSEEvent], model: str) -> dict[str, Any]:
    """Fold a list of Anthropic SSE events into a single Messages object."""
    message: dict[str, Any] = {
        "id": "msg_gateway",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }

    # index -> {"type": "text"/"tool_use", accumulators...}
    blocks: dict[int, dict[str, Any]] = {}

    for event in events:
        data = event.data or {}
        etype = event.event

        # OpenRouter relays its (Anthropic-format) SSE byte-for-byte as "raw"
        # events. In non-streaming mode we must parse those data lines back into
        # normalized events, otherwise the folded message comes out empty. Each
        # Anthropic data line's JSON carries a "type" equal to the event name.
        if etype == "raw":
            line = data.get("line", "")
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = data.get("type", "")

        if etype == "message_start":
            msg = data.get("message", {})
            message["id"] = msg.get("id", message["id"])
            if msg.get("model"):
                message["model"] = msg["model"]
            if isinstance(msg.get("usage"), dict):
                message["usage"].update(msg["usage"])

        elif etype == "content_block_start":
            idx = data.get("index", 0)
            cb = dict(data.get("content_block", {}))
            if cb.get("type") == "tool_use":
                cb["_json"] = ""
                cb.setdefault("input", {})
            elif cb.get("type") == "text":
                cb.setdefault("text", "")
            blocks[idx] = cb

        elif etype == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta", {})
            block = blocks.setdefault(idx, {"type": "text", "text": ""})
            if delta.get("type") == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
            elif delta.get("type") == "input_json_delta":
                block["_json"] = block.get("_json", "") + delta.get("partial_json", "")

        elif etype == "content_block_stop":
            idx = data.get("index", 0)
            stopped = blocks.get(idx)
            if stopped and stopped.get("type") == "tool_use":
                raw = stopped.pop("_json", "")
                try:
                    stopped["input"] = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    stopped["input"] = {}

        elif etype == "message_delta":
            delta = data.get("delta", {})
            if delta.get("stop_reason"):
                message["stop_reason"] = delta["stop_reason"]
            if delta.get("stop_sequence") is not None:
                message["stop_sequence"] = delta["stop_sequence"]
            if isinstance(data.get("usage"), dict):
                message["usage"].update(data["usage"])

    # Finalize any tool blocks that never received an explicit stop.
    for block in blocks.values():
        if block.get("type") == "tool_use" and "_json" in block:
            raw = block.pop("_json")
            try:
                block["input"] = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                block["input"] = {}

    message["content"] = [blocks[i] for i in sorted(blocks)]
    return message
