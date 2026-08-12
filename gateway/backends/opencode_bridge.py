"""
Opencode Backend

Exposes opencode's own hosted models through the gateway by driving a local
``opencode serve`` instance's session API (opencode is NOT OpenAI-compatible —
its HTTP surface is session-based: create a session, post a message, read the
assistant reply). We translate the inbound Anthropic Messages request into a
single opencode prompt and translate the reply back into Anthropic SSE events.

Model ids are flat: ``claude-opencode-<key>`` where <key> maps (via
``opencode_model_map``) to an opencode provider modelID. Text-only for now:
tool-use is not bridged through opencode's agentic loop.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from gateway.backends.base import BackendBase, BackendHealth, SSEEvent
from gateway.config import get_config
from gateway.translators.openai import _system_to_text


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Flatten Anthropic messages into a single prompt.

    The gateway is stateless per request (the client resends full history) but
    an opencode session is created fresh per request, so the whole conversation
    is rendered as a role-tagged transcript in one user turn.
    """
    def block_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(p for p in parts if p)
        return ""

    non_system = [m for m in messages if m.get("role") != "system"]
    # Single user turn: send its text verbatim (no role tags).
    if len(non_system) == 1 and non_system[0].get("role") == "user":
        return block_text(non_system[0].get("content"))

    lines = []
    for m in non_system:
        role = m.get("role", "user")
        text = block_text(m.get("content"))
        if text:
            lines.append(f"[{role}]: {text}")
    return "\n\n".join(lines)


class OpencodeBridgeBackend(BackendBase):
    """opencode-serve session-API backend (opencode's own hosted models)."""

    @property
    def provider_name(self) -> str:
        return "opencode"

    @property
    def model_prefix(self) -> str:
        return "claude-opencode-"

    def __init__(self) -> None:
        super().__init__("opencode")

    async def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        config = get_config()
        base = config.opencode_serve_url.rstrip("/")

        key = model[len(self.model_prefix):]
        model_id = config.opencode_model_map.get(key, key)

        system = _system_to_text(body.get("system"))
        prompt = _messages_to_prompt(body.get("messages", []))
        client = self.get_client()

        # 1. Create a throwaway session.
        session_id: str | None = None
        try:
            resp = await client.post(f"{base}/session", json={"title": "gateway"})
            if resp.status_code >= 400:
                yield self._error("session_create_failed",
                                  f"opencode serve create session HTTP {resp.status_code}: {resp.text[:200]}")
                return
            session_id = resp.json().get("id")
            if not session_id:
                yield self._error("session_create_failed", "opencode serve returned no session id")
                return

            # 2. Post the prompt and wait for the assistant reply.
            msg_body: dict[str, Any] = {
                "model": {"providerID": "opencode", "modelID": model_id},
                "agent": config.opencode_agent,
                "parts": [{"type": "text", "text": prompt}],
            }
            if system:
                msg_body["system"] = system

            resp = await client.post(f"{base}/session/{session_id}/message", json=msg_body)
            if resp.status_code >= 400:
                yield self._error("opencode_error",
                                  f"opencode serve message HTTP {resp.status_code}: {resp.text[:200]}")
                return

            reply = resp.json()
            text = self._extract_text(reply)

            # 3. Emit Anthropic SSE for the completed text.
            for event in self._text_events(text, model):
                yield event

        except Exception as exc:  # network / opencode serve down
            yield self._error("connection_error",
                              f"opencode serve unreachable at {base} ({exc}); is `opencode serve` running?")
        finally:
            if session_id:
                try:
                    await client.delete(f"{base}/session/{session_id}")
                except Exception:
                    pass

    @staticmethod
    def _extract_text(reply: dict[str, Any]) -> str:
        parts = reply.get("parts")
        if not isinstance(parts, list):
            return ""
        chunks = [p.get("text", "") for p in parts
                  if isinstance(p, dict) and p.get("type") == "text"]
        return "".join(chunks)

    @staticmethod
    def _error(err_type: str, message: str) -> SSEEvent:
        return SSEEvent(
            event="error",
            data={"type": "error", "error": {"type": err_type, "message": message}},
        )

    @staticmethod
    def _text_events(text: str, model: str) -> list[SSEEvent]:
        return [
            SSEEvent(event="message_start", data={
                "type": "message_start",
                "message": {
                    "id": "msg_opencode", "type": "message", "role": "assistant",
                    "model": model, "content": [], "stop_reason": None,
                    "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }),
            SSEEvent(event="content_block_start", data={
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            }),
            SSEEvent(event="content_block_delta", data={
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": text},
            }),
            SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
            SSEEvent(event="message_delta", data={
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0},
            }),
            SSEEvent(event="message_stop", data={"type": "message_stop"}),
        ]

    async def health_check(self) -> BackendHealth:
        base = get_config().opencode_serve_url.rstrip("/")
        try:
            resp = await self.get_client().get(f"{base}/config", timeout=3.0)
            healthy = resp.status_code == 200
            return BackendHealth(
                name=self.name, healthy=healthy,
                error=None if healthy else f"opencode serve HTTP {resp.status_code}",
            )
        except Exception as exc:
            return BackendHealth(name=self.name, healthy=False,
                                 error=f"opencode serve unreachable at {base} ({exc})")
