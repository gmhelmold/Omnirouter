"""
NVIDIA NIM Translation Backend

Reuses shared OpenAI translator.
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


class NimBackend(BackendBase):
    """NVIDIA NIM backend with Anthropic↔OpenAI translation."""

    @property
    def provider_name(self) -> str:
        return "nim"

    @property
    def model_prefix(self) -> str:
        return "claude-nim-"

    def __init__(self):
        super().__init__("nim")
        self._api_key: str | None = None
        self._base_url: str | None = None

    def _get_config(self):
        config = get_config()
        self._api_key = config.nim_api_key
        self._base_url = config.nim_base_url.rstrip("/") if config.nim_base_url else None

    async def handle_request(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        headers: dict,
        cwd: Optional[str] = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Translate Anthropic → OpenAI → NIM, stream back."""
        self._get_config()

        if not self._api_key or not self._base_url:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "auth_error", "message": "NIM_BASE_URL and NIM_API_KEY required"}}
            )
            return

        # Extract model key and map
        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.nim_model_map.get(model_key, model_key)

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
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
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
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "timeout", "message": "NIM request timed out"}})
        except httpx.HTTPError as e:
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "connection_error", "message": str(e)}})

    async def health_check(self) -> "BackendHealth":
        import time

        self._get_config()
        if not self._api_key or not self._base_url:
            return BackendHealth(name=self.name, healthy=False, error="NIM not configured")

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