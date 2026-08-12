"""
Groq Translation Backend

Uses shared OpenAI translator for Anthropic↔OpenAI conversion.
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


class GroqBackend(BackendBase):
    """Groq backend with Anthropic↔OpenAI translation."""

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_prefix(self) -> str:
        return "claude-groq-"

    def __init__(self) -> None:
        super().__init__("groq")
        self._api_key: str | None = None
        self._base_url: str = "https://api.groq.com/openai/v1"

    def _get_config(self) -> None:
        config = get_config()
        self._api_key = config.groq_api_key

    async def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Translate Anthropic -> OpenAI -> Groq, stream back."""
        self._get_config()

        if not self._api_key:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "auth_error", "message": "GROQ_API_KEY not configured"}},
            )
            return

        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.groq_model_map.get(model_key, model_key)

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
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        async for event in stream_openai_compatible(
            self.get_client(),
            f"{self._base_url}/chat/completions",
            request_body,
            upstream_headers,
            provider="Groq",
        ):
            yield event

    async def health_check(self) -> BackendHealth:
        import time

        self._get_config()
        if not self._api_key:
            return BackendHealth(name=self.name, healthy=False, error="No API key")

        start = time.time()
        client = self.get_client()
        try:
            response = await client.get(
                f"{self._base_url}/models",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=5.0
            )
            latency = (time.time() - start) * 1000
            if response.status_code == 200:
                return BackendHealth(name=self.name, healthy=True, latency_ms=latency)
            return BackendHealth(name=self.name, healthy=False, error=f"HTTP {response.status_code}")
        except Exception as e:
            return BackendHealth(name=self.name, healthy=False, error=str(e))
