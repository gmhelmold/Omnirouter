"""
Backend base protocol and common infrastructure.

Provides:
- Standardized async streaming interface (Anthropic SSE events)
- HTTP client lifecycle with pooling
- Health checking
- Header propagation
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx

from gateway.config import get_config


@dataclass
class SSEEvent:
    """Normalized SSE event in Anthropic streaming format.

    ``event`` is the SSE event name (message_start, content_block_start,
    content_block_delta, content_block_stop, message_delta, message_stop,
    error, ping). ``data`` is the JSON payload and always carries a matching
    ``type`` field. ``event == "raw"`` is a gateway-internal marker for
    byte-for-byte passthrough (OpenRouter).
    """

    event: str
    data: dict[str, Any]
    retry: int = 0


@dataclass
class BackendHealth:
    """Health status for a backend."""

    name: str
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None
    checked_at: float = field(default_factory=time.time)

    def is_stale(self, max_age: float = 30.0) -> bool:
        return time.time() - self.checked_at > max_age


class BackendBase(ABC):
    """Abstract base class for all provider backends."""

    def __init__(self, name: str):
        self.name = name
        self._health: BackendHealth | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier for routing (e.g., 'openrouter', 'groq')."""

    @property
    @abstractmethod
    def model_prefix(self) -> str:
        """Model ID prefix this backend handles (e.g., 'claude-openrouter-')."""

    def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with proper configuration."""
        if self._client is None or self._client.is_closed:
            config = get_config()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=config.connect_timeout,
                    read=config.request_timeout,
                    write=config.request_timeout,
                    pool=config.connect_timeout,
                ),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                ),
                http2=True,
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """
        Handle a Messages request and stream Anthropic SSE events.

        Args:
            model: Full model ID from the request (e.g. "claude-groq-llama3").
            body: The full Anthropic Messages request body. Backends read
                ``messages``, ``system``, ``tools``, ``max_tokens``,
                ``temperature``, ``top_p`` and ``stop_sequences`` from it so no
                request parameter is silently dropped.
            headers: Inbound request headers (for propagation).
            cwd: Working directory (opencode only).

        Yields:
            SSEEvent objects in Anthropic streaming format.
        """
        raise NotImplementedError

    async def health_check(self) -> BackendHealth:
        """Check backend health. Override for custom logic."""
        start = time.time()
        try:
            # Subclasses should override with a real upstream probe.
            return BackendHealth(
                name=self.name,
                healthy=True,
                latency_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return BackendHealth(name=self.name, healthy=False, error=str(e))

    def _propagate_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Extract and return headers to propagate to upstream."""
        propagated: dict[str, str] = {}
        for key in ("x-claude-code-session-id", "x-claude-code-agent-id", "anthropic-version", "anthropic-beta"):
            if key in headers:
                propagated[key] = headers[key]
        return propagated


def json_dumps(obj: Any) -> str:
    """Fast JSON serialization via orjson, falling back to stdlib json."""
    try:
        import orjson

        return orjson.dumps(obj).decode()
    except ImportError:
        import json

        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
