"""
OpenRouter Pass-Through Backend

Zero translation - pure byte-for-byte passthrough to OpenRouter's Anthropic Skin.
This is the simplest and most reliable backend since OpenRouter natively speaks Anthropic format.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx

from gateway.backends.base import BackendBase, BackendHealth, SSEEvent
from gateway.config import get_config


class OpenRouterBackend(BackendBase):
    """OpenRouter backend with pure Anthropic Skin passthrough."""

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def model_prefix(self) -> str:
        return "claude-openrouter-"

    def __init__(self) -> None:
        super().__init__("openrouter")
        self._api_key: str | None = None
        self._base_url: str | None = None

    def _get_config(self) -> None:
        config = get_config()
        self._api_key = config.openrouter_api_key
        self._base_url = config.openrouter_base_url.rstrip("/")

    async def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Pass-through to OpenRouter Anthropic Skin.

        Forwards the full Anthropic request body verbatim (system, max_tokens,
        temperature, top_p, stop_sequences, metadata, ...) with only the model
        remapped and streaming forced on.
        """
        self._get_config()

        if not self._api_key:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "auth_error", "message": "OPENROUTER_API_KEY not configured"}},
            )
            return

        # Strip prefix and map model
        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.openrouter_model_map.get(model_key, model_key)

        # Forward the whole body unchanged, only overriding model + stream.
        request_body = {k: v for k, v in body.items() if k != "model"}
        request_body["model"] = mapped_model
        request_body["stream"] = True

        # Prepare headers
        upstream_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "anthropic-version": "2023-06-01",
        }
        # Propagate relevant headers
        for key in ("x-claude-code-session-id", "x-claude-code-agent-id", "anthropic-beta"):
            if key in headers:
                upstream_headers[key] = headers[key]

        client = self.get_client()

        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                json=request_body,
                headers=upstream_headers,
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
            ) as response:
                if response.status_code >= 400:
                    error_data = await response.aread()
                    yield SSEEvent(
                        event="error",
                        data={
                            "type": "error",
                            "error": {
                                "type": "api_error",
                                "message": f"OpenRouter {response.status_code}: {error_data.decode()[:500]}"
                            }
                        }
                    )
                    return

                # Byte-for-byte SSE relay
                async for line in response.aiter_lines():
                    if line.strip():
                        yield SSEEvent(
                            event="raw",  # Special marker for raw passthrough
                            data={"line": line}
                        )

        except httpx.TimeoutException:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "timeout", "message": "Request to OpenRouter timed out"}}
            )
        except httpx.HTTPError as e:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "connection_error", "message": str(e)}}
            )

    async def health_check(self) -> BackendHealth:
        """Health check via OpenRouter models endpoint."""
        import time

        self._get_config()
        if not self._api_key:
            return BackendHealth(name=self.name, healthy=False, error="No API key")

        start = time.time()
        client = self.get_client()
        try:
            response = await client.get(
                f"{self._base_url}/v1/models",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=5.0
            )
            latency = (time.time() - start) * 1000
            if response.status_code == 200:
                return BackendHealth(name=self.name, healthy=True, latency_ms=latency)
            return BackendHealth(name=self.name, healthy=False, error=f"HTTP {response.status_code}")
        except Exception as e:
            return BackendHealth(name=self.name, healthy=False, error=str(e))
