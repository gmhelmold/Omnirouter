"""
Mistral API Translation Backend

Reuses shared OpenAI translator. Mistral API is OpenAI-compatible.
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


class MistralBackend(BackendBase):
    """Mistral API backend with Anthropic↔OpenAI translation."""

    @property
    def provider_name(self) -> str:
        return "mistral"

    @property
    def model_prefix(self) -> str:
        return "claude-mistral-"

    def __init__(self) -> None:
        super().__init__("mistral")
        self._api_key: str | None = None
        self._base_url: str = "https://api.mistral.ai/v1"
        self._rate_limit_tokens: int = 500  # per minute
        self._rate_limit_refill: float = 500.0 / 60.0
        self._rate_limit_last: float = 0.0
        self._rate_limit_current: float = 500.0

    def _get_config(self) -> None:
        config = get_config()
        self._api_key = config.mistral_api_key
        self._rate_limit_tokens = config.mistral_rpm
        self._rate_limit_refill = config.mistral_rpm / 60.0

    async def _acquire_rate_limit(self) -> None:
        """Token bucket rate limiting."""
        import asyncio
        import time

        now = time.time()
        if self._rate_limit_last > 0:
            elapsed = now - self._rate_limit_last
            self._rate_limit_current = min(
                self._rate_limit_tokens,
                self._rate_limit_current + elapsed * self._rate_limit_refill
            )
        self._rate_limit_last = now

        if self._rate_limit_current < 1.0:
            wait_time = (1.0 - self._rate_limit_current) / self._rate_limit_refill
            await asyncio.sleep(wait_time)
            self._rate_limit_current = 0.0
        else:
            self._rate_limit_current -= 1.0

    async def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Translate Anthropic -> OpenAI -> Mistral, stream back."""
        self._get_config()

        if not self._api_key:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "auth_error", "message": "MISTRAL_API_KEY not configured"}},
            )
            return

        await self._acquire_rate_limit()

        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.mistral_model_map.get(model_key, model_key)

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

        async for event in stream_openai_compatible(
            self.get_client(),
            f"{self._base_url}/chat/completions",
            request_body,
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            provider="Mistral",
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
