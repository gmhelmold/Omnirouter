"""
Opencode Bridge Backend (Multi-Instance)

Routes to opencode-bridge instances (one per provider) using shared OpenAI translator.
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
    """Opencode-bridge backend with multi-instance routing."""

    @property
    def provider_name(self) -> str:
        return "opencode"

    @property
    def model_prefix(self) -> str:
        return "claude-opencode-"

    def __init__(self) -> None:
        super().__init__("opencode-bridge")
        self._endpoints: dict[str, str] = {}
        self._model_map: dict[str, dict[str, str]] = {}

    def _get_config(self) -> None:
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
        for provider in self._endpoints:
            provider_prefix = f"{provider}-"
            if remainder.startswith(provider_prefix):
                model_key = remainder[len(provider_prefix):]
                return provider, model_key
        return None

    async def handle_request(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Route to correct opencode-bridge instance."""
        self._get_config()

        parsed = self._parse_model(model)
        if not parsed:
            yield SSEEvent(
                event="error",
                data={
                    "type": "error",
                    "error": {"type": "invalid_model", "message": f"Invalid opencode model format: {model}"},
                },
            )
            return

        provider, model_key = parsed
        endpoint = self._endpoints.get(provider)
        if not endpoint:
            yield SSEEvent(
                event="error",
                data={
                    "type": "error",
                    "error": {"type": "config_error", "message": f"No bridge endpoint for provider: {provider}"},
                },
            )
            return

        provider_model_map = self._model_map.get(provider, {})
        mapped_model = provider_model_map.get(model_key, model_key)

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
            f"{endpoint}/v1/chat/completions",
            request_body,
            {"Content-Type": "application/json", "Accept": "text/event-stream"},
            provider=f"opencode-bridge ({provider})",
        ):
            yield event

    async def health_check(self) -> BackendHealth:
        self._get_config()
        results = []
        overall_healthy = True

        for provider, endpoint in self._endpoints.items():
            client = self.get_client()
            try:
                response = await client.get(f"{endpoint}/health", timeout=3.0)
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
