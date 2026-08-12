"""
Anthropic Pass-Through Backend

Catch-all for native Anthropic model ids (claude-opus-*, claude-sonnet-*,
claude-haiku-*, claude-3-*, ...). Forwards the request byte-for-byte to
api.anthropic.com, reusing the CLIENT's own credentials/headers so a Claude
Code subscription (Max/Pro OAuth) or an API key keeps working through the
gateway. The gateway never stores an Anthropic credential — it only relays
whatever the client sent (authorization / x-api-key / anthropic-beta).

Registered LAST so provider-specific prefixes (claude-groq-, claude-openrouter-,
...) match their own backends first; anything else that still starts with
"claude-" is treated as a native Anthropic model and passed through.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from gateway.backends.base import BackendBase, BackendHealth, SSEEvent

_UPSTREAM = os.environ.get("ANTHROPIC_UPSTREAM_URL", "https://api.anthropic.com").rstrip("/")

# Headers to relay from the client to Anthropic. Includes the credential the
# client presented (OAuth bearer for subscriptions, or x-api-key for API keys).
_RELAY_HEADERS = (
    "authorization",
    "x-api-key",
    "anthropic-version",
    "anthropic-beta",
    "x-claude-code-session-id",
    "x-claude-code-agent-id",
)


class AnthropicBackend(BackendBase):
    """Native Anthropic passthrough that reuses the client's credentials."""

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_prefix(self) -> str:
        # Catch-all: any remaining "claude-*" id that no provider backend claimed.
        return "claude-"

    def __init__(self) -> None:
        super().__init__("anthropic")

    async def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Relay the request to api.anthropic.com verbatim using client auth."""
        # Forward the whole body unchanged (model id is already native), stream on.
        request_body = dict(body)
        request_body["model"] = model
        request_body["stream"] = True

        upstream_headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        lower = {k.lower(): v for k, v in headers.items()}
        for key in _RELAY_HEADERS:
            if key in lower:
                upstream_headers[key] = lower[key]
        upstream_headers.setdefault("anthropic-version", "2023-06-01")

        if "authorization" not in upstream_headers and "x-api-key" not in upstream_headers:
            yield SSEEvent(
                event="error",
                data={
                    "type": "error",
                    "error": {
                        "type": "auth_error",
                        "message": (
                            "No Anthropic credential in request. For a Max/Pro subscription set "
                            "ONLY ANTHROPIC_BASE_URL (no ANTHROPIC_AUTH_TOKEN) so Claude Code keeps "
                            "sending its login through the gateway."
                        ),
                    },
                },
            )
            return

        client = self.get_client()
        try:
            async with client.stream(
                "POST",
                f"{_UPSTREAM}/v1/messages",
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
                                "message": f"Anthropic {response.status_code}: {error_data.decode()[:500]}",
                            },
                        },
                    )
                    return

                async for line in response.aiter_lines():
                    if line.strip():
                        yield SSEEvent(event="raw", data={"line": line})

        except httpx.TimeoutException:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "timeout", "message": "Request to Anthropic timed out"}},
            )
        except httpx.HTTPError as e:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "connection_error", "message": str(e)}},
            )

    async def health_check(self) -> BackendHealth:
        # No stored credential to check; the passthrough is healthy if the process is up.
        # Actual auth is validated per-request using the client's own credential.
        return BackendHealth(name=self.name, healthy=True)
