"""
Opencode Bridge Backend (Multi-Instance)

Routes to opencode-bridge instances (one per provider) using shared OpenAI translator.
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


class OpencodeBridgeBackend(BackendBase):
    """Opencode-bridge backend with multi-instance routing."""

    @property
    def provider_name(self) -> str:
        return "opencode"

    @property
    def model_prefix(self) -> str:
        return "claude-opencode-"

    def __init__(self):
        super().__init__("opencode-bridge")
        self._endpoints: dict[str, str] = {}
        self._model_map: dict[str, dict[str, str]] = {}

    def _get_config(self):
        config = get_config()
        self._endpoints = config.opencode_bridge_endpoints
        self._model_map = config.opencode_bridge_model_map

    def _parse_model(self, model: str) -> tuple[str, str] | None:
        """
        Parse model ID: claude-opencode-{provider}-{model_key}
        Returns (provider, model_key) or None if invalid.
        """
        prefix_len = len(self.model_prefix)
        if not model.startswith(self.model_prefix):
            return None
        remainder = model[prefix_len:]
        # Find provider (groq, gemini, mistral)
        for provider in self._endpoints.keys():
            provider_prefix = f"{provider}-"
            if remainder.startswith(provider_prefix):
                model_key = remainder[len(provider_prefix):]
                return provider, model_key
        return None

    async def handle_request(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        headers: dict,
        cwd: Optional[str] = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Route to correct opencode-bridge instance."""
        self._get_config()

        parsed = self._parse_model(model)
        if not parsed:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "invalid_model", "message": f"Invalid opencode model format: {model}"}}
            )
            return

        provider, model_key = parsed
        endpoint = self._endpoints.get(provider)
        if not endpoint:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "config_error", "message": f"No bridge endpoint for provider: {provider}"}}
            )
            return

        # Map model key
        provider_model_map = self._model_map.get(provider, {})
        mapped_model = provider_model_map.get(model_key, model_key)

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
                f"{endpoint}/v1/chat/completions",
                json=request_body,
                headers={
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
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "timeout", "message": f"Opencode bridge ({provider}) request timed out"}})
        except httpx.HTTPError as e:
            yield SSEEvent(event="error", data={"type": "error", "error": {"type": "connection_error", "message": str(e)}})

    async def health_check(self) -> BackendHealth:
        import time

        self._get_config()
        results = []
        overall_healthy = True

        for provider, endpoint in self._endpoints.items():
            start = time.time()
            client = self.get_client()
            try:
                response = await client.get(f"{endpoint}/health", timeout=3.0)
                latency = (time.time() - start) * 1000
                if response.status_code == 200:
                    results.append(f"{provider}:healthy")
                else:
                    results.append(f"{provider}:unhealthy")
                    overall_healthy = False
            except Exception as e:
                results.append(f"{provider}:error({e})")
                overall_healthy = False

        return BackendHealth(
            name=self.name,
            healthy=overall_healthy,
            error=None if overall_healthy else "; ".join(results)
        )