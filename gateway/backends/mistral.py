"""
Mistral API Translation Backend

Reuses shared OpenAI translator. Mistral API is OpenAI-compatible.
"""

from __future__ import annotations

from typing import AsyncGenerator, Optional

import httpx

from gateway.backends.base import BackendBase, SSEEvent, BackendHealth
from gateway.translators.openai import (
    anthropic_to_openai,
    openai_sse_to_anthropic,
    normalize_openai_error,
    build_openai_request,
    OpenAIStreamBuffer,
)
from gateway.config import get_config


class MistralBackend(BackendBase):
    """Mistral API backend with Anthropic↔OpenAI translation."""

    @property
    def provider_name(self) -> str:
        return "mistral"

    @property
    def model_prefix(self) -> str:
        return "claude-mistral-"

    def __init__(self):
        super().__init__("mistral")
        self._api_key: str | None = None
        self._base_url: str = "https://api.mistral.ai/v1"
        self._rate_limit_tokens: int = 500  # per minute
        self._rate_limit_refill: float = 500.0 / 60.0
        self._rate_limit_last: float = 0.0
        self._rate_limit_current: float = 500.0

    def _get_config(self):
        config = get_config()
        self._api_key = config.mistral_api_key
        self._rate_limit_tokens = config.mistral_rpm
        self._rate_limit_refill = config.mistral_rpm / 60.0

    async def _acquire_rate_limit(self):
        """Token bucket rate limiting."""
        import time
        import asyncio

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
        messages: list[dict],
        tools: list[dict] | None,
        headers: dict,
        cwd: Optional[str] = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Translate Anthropic → OpenAI → Mistral, stream back."""
        self._get_config()

        if not self._api_key:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "auth_error", "message": "MISTRAL_API_KEY not configured"}}
            )
            return

        # Rate limit
        await self._acquire_rate_limit()

        # Extract model key and map
        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.mistral_model_map.get(model_key, model_key)

        # Translate Anthropic → OpenAI
        openai_messages, openai_functions = anthropic_to_openai(messages, tools)

        # Build request
        request_body = build_openai_request(
            model=mapped_model,
            messages=openai_messages,
            tools=openai_functions,
            stream=True,
        )

        client = self.get_client()
        buffer = OpenAIStreamBuffer()

        try:
            await self._acquire_rate_limit()

            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=request_body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
            ) as response:
                if response.status_code >= 400:
                    yield SSEEvent(event="error", data=normalize_openai_error(response))
                    return

                buffer = OpenAIStreamBuffer()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    event, buffer = openai_sse_to_anthropic(line, buffer)
                    if event:
                        yield event

        except httpx.TimeoutException:
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "timeout", "message": "Mistral request timed out"}})
        except httpx.HTTPError as e:
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "connection_error", "message": str(e)}})

    async def health_check(self) -> "BackendHealth":
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