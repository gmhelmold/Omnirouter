"""
OpenCode Zen Backend

opencode's own hosted models ("OpenCode Zen") are served at an OpenAI-compatible
endpoint (https://opencode.ai/zen/v1, provider spec `@ai-sdk/openai-compatible`,
free models keyed by the public API key). We call it DIRECTLY — not through
`opencode serve`'s session/agent API — so tool-use works: the shared OpenAI
translator forwards Claude Code's tool schemas and maps the model's tool_calls
back to Anthropic tool_use, exactly like the Groq / Mistral backends.

Model ids are flat: ``claude-opencode-<key>`` where <key> maps (via
``opencode_model_map``) to a Zen modelID (e.g. big-pickle, deepseek-v4-flash-free).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from gateway.backends.base import BackendBase, BackendHealth, SSEEvent
from gateway.config import get_config
from gateway.translators.openai import (
    anthropic_to_openai,
    build_openai_request,
    stream_openai_compatible,
)


class OpencodeBridgeBackend(BackendBase):
    """OpenCode Zen backend (direct, OpenAI-compatible, tool-capable)."""

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
        """Translate Anthropic -> OpenAI -> OpenCode Zen, stream back."""
        config = get_config()
        base = config.opencode_base_url.rstrip("/")
        api_key = config.opencode_api_key or "public"

        key = model[len(self.model_prefix):]
        mapped_model = config.opencode_model_map.get(key, key)

        openai_messages, openai_functions = anthropic_to_openai(
            body.get("messages", []), body.get("tools"), body.get("system")
        )
        request_body = build_openai_request(
            model=mapped_model,
            messages=openai_messages,
            tools=openai_functions,
            stream=True,
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
            max_tokens=body.get("max_tokens"),
            stop=body.get("stop_sequences"),
        )

        upstream_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        async for event in stream_openai_compatible(
            self.get_client(),
            f"{base}/chat/completions",
            request_body,
            upstream_headers,
            provider="OpenCode Zen",
        ):
            yield event

    async def health_check(self) -> BackendHealth:
        import time

        config = get_config()
        base = config.opencode_base_url.rstrip("/")
        api_key = config.opencode_api_key or "public"

        start = time.time()
        try:
            response = await self.get_client().get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )
            latency = (time.time() - start) * 1000
            if response.status_code == 200:
                return BackendHealth(name=self.name, healthy=True, latency_ms=latency)
            return BackendHealth(name=self.name, healthy=False, error=f"HTTP {response.status_code}")
        except Exception as e:
            return BackendHealth(name=self.name, healthy=False, error=str(e))
