"""
Tests for the model router and its fallback semantics.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest

from gateway.backends.base import BackendBase, SSEEvent
from gateway.router import ModelRouter


class StubBackend(BackendBase):
    def __init__(self, provider: str, prefix: str, script: Callable[[str], list[SSEEvent]]):
        super().__init__(provider)
        self._provider = provider
        self._prefix = prefix
        self._script = script
        self.received_model: str | None = None

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_prefix(self) -> str:
        return self._prefix

    async def handle_request(
        self, model: str, body: dict[str, Any], headers: dict, cwd=None
    ) -> AsyncGenerator[SSEEvent, None]:
        self.received_model = model
        for event in self._script(model):
            yield event


def _ok(_model: str) -> list[SSEEvent]:
    return [
        SSEEvent(event="message_start", data={"type": "message_start"}),
        SSEEvent(event="content_block_delta", data={"type": "content_block_delta"}),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]


def _retryable_error(_model: str) -> list[SSEEvent]:
    return [SSEEvent(event="error", data={"type": "error", "error": {"type": "rate_limit", "message": "429"}})]


def _fatal_error(_model: str) -> list[SSEEvent]:
    return [SSEEvent(event="error", data={"type": "error", "error": {"type": "auth_error", "message": "bad key"}})]


def _content_then_error(_model: str) -> list[SSEEvent]:
    return [
        SSEEvent(event="message_start", data={"type": "message_start"}),
        SSEEvent(event="content_block_delta", data={"type": "content_block_delta"}),
        SSEEvent(event="error", data={"type": "error", "error": {"type": "timeout", "message": "late"}}),
    ]


async def _collect(gen) -> list[SSEEvent]:
    return [e async for e in gen]


@pytest.mark.asyncio
async def test_primary_success_no_fallback():
    primary = StubBackend("groq", "claude-groq-", _ok)
    fb = StubBackend("opencode", "claude-opencode-", _ok)
    router = ModelRouter()
    router.initialize([primary, fb])
    router._fallback_chains = {"groq": ["opencode"]}

    events = await _collect(router.route_with_fallback("claude-groq-llama3", {}, {}))
    assert [e.event for e in events] == ["message_start", "content_block_delta", "message_stop"]
    assert fb.received_model is None  # fallback untouched


@pytest.mark.asyncio
async def test_fallback_on_early_retryable_error():
    primary = StubBackend("groq", "claude-groq-", _retryable_error)
    fb = StubBackend("opencode", "claude-opencode-", _ok)
    router = ModelRouter()
    router.initialize([primary, fb])
    router._fallback_chains = {"groq": ["opencode"]}

    events = await _collect(router.route_with_fallback("claude-groq-llama3", {}, {}))
    assert [e.event for e in events] == ["message_start", "content_block_delta", "message_stop"]
    # model key preserved, remapped to opencode provider form
    assert fb.received_model == "claude-opencode-groq-llama3"


@pytest.mark.asyncio
async def test_fatal_error_no_fallback():
    primary = StubBackend("groq", "claude-groq-", _fatal_error)
    fb = StubBackend("opencode", "claude-opencode-", _ok)
    router = ModelRouter()
    router.initialize([primary, fb])
    router._fallback_chains = {"groq": ["opencode"]}

    events = await _collect(router.route_with_fallback("claude-groq-llama3", {}, {}))
    assert len(events) == 1
    assert events[0].data["error"]["type"] == "auth_error"
    assert fb.received_model is None


@pytest.mark.asyncio
async def test_error_after_content_is_surfaced_not_retried():
    primary = StubBackend("groq", "claude-groq-", _content_then_error)
    fb = StubBackend("opencode", "claude-opencode-", _ok)
    router = ModelRouter()
    router.initialize([primary, fb])
    router._fallback_chains = {"groq": ["opencode"]}

    events = await _collect(router.route_with_fallback("claude-groq-llama3", {}, {}))
    names = [e.event for e in events]
    assert names == ["message_start", "content_block_delta", "error"]
    assert fb.received_model is None  # never retried mid-stream


@pytest.mark.asyncio
async def test_unknown_model():
    router = ModelRouter()
    router.initialize([StubBackend("groq", "claude-groq-", _ok)])
    events = await _collect(router.route_with_fallback("claude-unknown-x", {}, {}))
    assert events[0].data["error"]["type"] == "model_not_found"


@pytest.mark.asyncio
async def test_all_fallbacks_failed():
    primary = StubBackend("groq", "claude-groq-", _retryable_error)
    fb = StubBackend("opencode", "claude-opencode-", _retryable_error)
    router = ModelRouter()
    router.initialize([primary, fb])
    router._fallback_chains = {"groq": ["opencode"]}

    events = await _collect(router.route_with_fallback("claude-groq-llama3", {}, {}))
    assert events[-1].data["error"]["type"] == "all_fallbacks_failed"
