"""
Gemini Translation Backend

Uses shared Gemini translator for Anthropic↔Gemini conversion.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx

from gateway.backends.base import BackendBase, BackendHealth, SSEEvent
from gateway.config import get_config
from gateway.translators.gemini import (
    GeminiTranslator,
    anthropic_to_gemini,
    build_generation_config,
    normalize_gemini_error,
)


class GeminiBackend(BackendBase):
    """Google AI Studio (Gemini) backend with Anthropic↔Gemini translation."""

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_prefix(self) -> str:
        return "claude-gemini-"

    def __init__(self) -> None:
        super().__init__("gemini")
        self._api_key: str | None = None
        self._base_url: str = "https://generativelanguage.googleapis.com/v1beta"
        self._rate_limit_tokens: int = 15  # per minute
        self._rate_limit_refill: float = 15.0 / 60.0  # tokens per second
        self._rate_limit_last: float = 0.0
        self._rate_limit_current: float = 15.0

    def _get_config(self) -> None:
        config = get_config()
        self._api_key = config.gemini_api_key
        self._rate_limit_tokens = config.gemini_rpm
        self._rate_limit_refill = config.gemini_rpm / 60.0

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
            # Wait until we have a token
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
        """Translate Anthropic -> Gemini, stream back."""
        self._get_config()

        if not self._api_key:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "auth_error", "message": "GEMINI_API_KEY not configured"}},
            )
            return

        await self._acquire_rate_limit()

        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.gemini_model_map.get(model_key, model_key)

        generation_config = build_generation_config(
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
            max_tokens=body.get("max_tokens"),
            stop=body.get("stop_sequences"),
        )
        gemini_request = anthropic_to_gemini(
            body.get("messages", []),
            body.get("tools"),
            body.get("system"),
            generation_config or None,
        )

        translator = GeminiTranslator()
        url = (
            f"{self._base_url}/models/{mapped_model}:streamGenerateContent"
            f"?alt=sse&key={self._api_key}"
        )
        try:
            async with self.get_client().stream(
                "POST",
                url,
                json=gemini_request,
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    yield SSEEvent(event="error", data=normalize_gemini_error(response))
                    return
                async for line in response.aiter_lines():
                    for event in translator.feed(line):
                        yield event
            for event in translator.flush():
                yield event
        except httpx.TimeoutException:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "timeout", "message": "Gemini request timed out"}},
            )
        except httpx.HTTPError as e:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "connection_error", "message": str(e)}},
            )

    async def health_check(self) -> BackendHealth:
        import time

        self._get_config()
        if not self._api_key:
            return BackendHealth(name=self.name, healthy=False, error="No API key")

        start = time.time()
        try:
            response = await self.get_client().get(
                f"{self._base_url}/models?key={self._api_key}",
                timeout=5.0,
            )
            latency = (time.time() - start) * 1000
            if response.status_code == 200:
                return BackendHealth(name=self.name, healthy=True, latency_ms=latency)
            return BackendHealth(name=self.name, healthy=False, error=f"HTTP {response.status_code}")
        except Exception as e:
            return BackendHealth(name=self.name, healthy=False, error=str(e))
