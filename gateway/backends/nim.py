"""
NVIDIA NIM Translation Backend

Reuses shared OpenAI translator.
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


class NimBackend(BackendBase):
    """NVIDIA NIM backend with Anthropic↔OpenAI translation."""

    @property
    def provider_name(self) -> str:
        return "nim"

    @property
    def model_prefix(self) -> str:
        return "claude-nim-"

    def __init__(self) -> None:
        super().__init__("nim")
        self._api_key: str | None = None
        self._base_url: str | None = None

    def _get_config(self) -> None:
        config = get_config()
        self._api_key = config.nim_api_key
        self._base_url = config.nim_base_url.rstrip("/") if config.nim_base_url else None

    async def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Translate Anthropic -> OpenAI -> NIM, stream back."""
        self._get_config()

        if not self._api_key or not self._base_url:
            yield SSEEvent(
                event="error",
                data={
                    "type": "error",
                    "error": {"type": "auth_error", "message": "NIM_BASE_URL and NIM_API_KEY required"},
                },
            )
            return

        config = get_config()
        model_key = model[len(self.model_prefix):]
        mapped_model = config.nim_model_map.get(model_key, model_key)

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
            f"{self._base_url}/v1/chat/completions",
            request_body,
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            provider="NIM",
        ):
            yield event

    async def health_check(self) -> BackendHealth:
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
