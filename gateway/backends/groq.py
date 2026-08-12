"""
Groq Translation Backend

Uses shared OpenAI translator for Anthropic↔OpenAI conversion.
"""

from __future__ import annotations

from typing import AsyncGenerator, Optional

import httpx

from gateway.backends.base import BackendBase, SSEEvent
from gateway.translators.openai import (
    anthropic_to_openai,
    openai_sse_to_anthropic,
    normalize_openai_error,
    build_openai_request,
    OpenAIStreamBuffer,
)
from gateway.config import get_config


class GroqBackend(BackendBase):
    """Groq backend with Anthropic↔OpenAI translation."""

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_prefix(self) -> str:
        return "claude-groq-"

    def __init__(self):
        super().__init__("groq")
        self._api_key: str | None = None
        self._base_url: str = "https://api.groq.com/openai/v1"

    def _get_config(self):
        config = get_config()
        self._api_key = config.groq_api_key

    async def handle_request(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        headers: dict,
        cwd: Optional[str] = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Translate Anthropic → OpenAI → Groq, stream back."""
        self._get_config()

        if not self._api_key:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "auth_error", "message": "GROQ_API_KEY not configured"}}
            )
            return

        # Extract model key and map
        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.groq_model_map.get(model_key, model_key)

        # Translate Anthropic → OpenAI
        openai_messages, openai_functions = anthropic_to_openai(messages, tools)

        # Build OpenAI request
        request_body = build_openai_request(
            model=mapped_model,
            messages=openai_messages,
            tools=openai_functions,
            stream=True,
        )

        # Prepare headers
        upstream_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        client = self.get_client()
        buffer = OpenAIStreamBuffer()

        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=request_body,
                headers=upstream_headers,
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
            ) as response:
                if response.status_code >= 400:
                    yield SSEEvent(event="error", data=normalize_openai_error(response))
                    return

                # Stream and translate SSE
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    event, buffer = openai_sse_to_anthropic(line, buffer)
                    if event:
                        yield event

        except httpx.TimeoutException:
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "timeout", "message": "Groq request timed out"}})
        except httpx.HTTPError as e:
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "connection_error", "message": str(e)}})

    async def health_check(self):
        from gateway.backends.base import BackendHealth
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