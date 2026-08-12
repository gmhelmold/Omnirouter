"""
Backend Base Protocol and Common Infrastructure

State-of-the-art backend abstraction with:
- Standardized async interface
- Health checking
- Request/response normalization
- Proper error handling
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from gateway.config import get_config


@dataclass
class SSEEvent:
    """Normalized SSE event for Anthropic format."""
    event: str  # message_start, content_block_delta, message_stop, message_delta, error
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

    @property
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
        pass

    @property
    @abstractmethod
    def model_prefix(self) -> str:
        """Model ID prefix this backend handles (e.g., 'claude-openrouter-')."""
        pass

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
    async def handle_request(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        headers: dict[str, str],
        cwd: Optional[str] = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """
        Handle a chat completion request.

        Args:
            model: Full model ID from request (e.g., "claude-openrouter-opus-5")
            messages: Anthropic format messages
            tools: Anthropic format tools
            headers: Request headers (for propagation)
            cwd: Working directory (for opencode)

        Yields:
            SSEEvent objects in Anthropic format
        """
        pass

    async def health_check(self) -> BackendHealth:
        """Check backend health. Override for custom logic."""
        start = time.time()
        try:
            # Default: try a simple GET to base URL
            client = self.get_client()
            # Subclasses should implement proper health check
            return BackendHealth(
                name=self.name,
                healthy=True,
                latency_ms=(time.time() - start) * 1000
            )
        except Exception as e:
            return BackendHealth(
                name=self.name,
                healthy=False,
                error=str(e)
            )

    def _propagate_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Extract and return headers to propagate to upstream."""
        propagated: dict[str, str] = {}
        for key in ("x-claude-code-session-id", "x-claude-code-agent-id", "anthropic-version", "anthropic-beta"):
            if key in headers:
                propagated[key] = headers[key]
        return propagated

    def _make_sse_response(self, event_generator: AsyncGenerator[SSEEvent, None]) -> StreamingResponse:
        """Create a StreamingResponse from SSE event generator."""
        async def sse_stream() -> AsyncGenerator[str, None]:
            async for event in event_generator:
                if event.event:
                    yield f"event: {event.event}\n"
                if event.data is not None:
                    import orjson
                    yield f"data: {orjson.dumps(event.data).decode()}\n"
                if event.retry:
                    yield f"retry: {event.retry}\n"
                yield "\n"

        return StreamingResponse(
            sse_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )


def json_dumps(obj: Any) -> str:
    """Fast JSON serialization."""
    import orjson
    return orjson.dumps(obj).decode()


# Import orjson at module level for performance
try:
    import orjson
except ImportError:
    import json
    def json_dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)