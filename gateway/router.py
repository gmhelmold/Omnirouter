"""
Request router with fallback-chain support.

- Model-prefix based routing to the owning backend.
- Configurable fallback chains per provider (see gateway_config.yaml).
- Fallback is only attempted before any content has been streamed to the
  client. Once a backend has emitted a real event, a later upstream failure is
  surfaced as-is instead of silently retrying on top of a half-sent stream
  (which would corrupt the response).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from gateway.backends.base import BackendBase, SSEEvent
from gateway.config import get_config

logger = logging.getLogger(__name__)

# Error types that are safe to retry on the next backend in the chain.
_RETRYABLE_ERRORS = {"timeout", "connection_error", "api_error", "rate_limit", "overloaded"}


class ModelRouter:
    """Routes requests to the appropriate backend with fallback support."""

    def __init__(self) -> None:
        self._backends: dict[str, BackendBase] = {}
        self._fallback_chains: dict[str, list[str]] = {}
        self._initialized = False

    def initialize(self, backends: list[BackendBase]) -> None:
        """Register backends and load fallback chains."""
        for backend in backends:
            self._backends[backend.provider_name] = backend
        self._fallback_chains = get_config().fallback_chains
        self._initialized = True
        logger.info("Router initialized with backends: %s", list(self._backends.keys()))

    def get_backend_for_model(self, model: str) -> BackendBase | None:
        """Find the backend that owns this model prefix."""
        for backend in self._backends.values():
            if model.startswith(backend.model_prefix):
                return backend
        return None

    def get_fallback_chain(self, primary_backend: str) -> list[str]:
        return self._fallback_chains.get(primary_backend, [])

    async def route_with_fallback(
        self,
        model: str,
        body: dict[str, Any],
        headers: dict[str, str],
        cwd: str | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Route a request, falling back through the chain on early failure."""
        if not self._initialized:
            raise RuntimeError("Router not initialized")

        primary = self.get_backend_for_model(model)
        if not primary:
            yield SSEEvent(
                event="error",
                data={
                    "type": "error",
                    "error": {"type": "model_not_found", "message": f"No backend for model: {model}"},
                },
            )
            return

        # Chain always starts with the primary, then its configured fallbacks.
        # De-duplicate while preserving order.
        seen: set[str] = set()
        chain: list[str] = []
        for name in [primary.provider_name, *self.get_fallback_chain(primary.provider_name)]:
            if name not in seen:
                seen.add(name)
                chain.append(name)

        config = get_config()
        max_attempts = min(config.max_fallback_attempts, len(chain))
        last_error: dict[str, Any] | None = None

        for attempt_idx, backend_name in enumerate(chain[:max_attempts]):
            backend = self._backends.get(backend_name)
            if not backend:
                logger.warning("Fallback backend not registered: %s", backend_name)
                continue

            target_model = self._map_model_for_backend(model, primary, backend)
            if not target_model:
                logger.warning("Cannot map model %s for backend %s", model, backend_name)
                continue

            logger.info(
                "Attempt %d/%d: routing to %s with model %s",
                attempt_idx + 1, max_attempts, backend_name, target_model,
            )

            emitted = False
            failed = False
            try:
                async for event in backend.handle_request(target_model, body, headers, cwd):
                    if event.event == "error" and not emitted:
                        error_data = event.data.get("error", {})
                        error_type = error_data.get("type", "")
                        if error_type in _RETRYABLE_ERRORS:
                            last_error = error_data
                            failed = True
                            logger.warning("Backend %s failed with %s, trying next", backend_name, error_type)
                            break
                        # Non-retryable error before any content: surface and stop.
                        yield event
                        return
                    emitted = True
                    yield event
            except Exception as exc:
                last_error = {"type": "exception", "message": str(exc)}
                logger.exception("Backend %s raised", backend_name)
                if emitted:
                    # Mid-stream crash: cannot safely retry, surface it.
                    yield SSEEvent(
                        event="error",
                        data={"type": "error", "error": last_error},
                    )
                    return
                failed = True

            if not failed:
                return  # success

        yield SSEEvent(
            event="error",
            data={
                "type": "error",
                "error": {
                    "type": "all_fallbacks_failed",
                    "message": f"All {len(chain)} backend(s) failed for model {model}",
                    "last_error": last_error,
                },
            },
        )

    def _split_model(self, model: str, backend: BackendBase) -> tuple[str, str] | None:
        """Return (provider, model_key) for a model owned by ``backend``."""
        if not model.startswith(backend.model_prefix):
            return None
        remainder = model[len(backend.model_prefix):]
        # opencode is flat: claude-opencode-{key} (provider is always opencode).
        return backend.provider_name, remainder

    def _map_model_for_backend(
        self, original_model: str, source: BackendBase, target: BackendBase
    ) -> str | None:
        """Map a model ID from the source backend into the target's format."""
        if target is source:
            return original_model

        split = self._split_model(original_model, source)
        if not split:
            return None
        _, key = split
        return f"{target.model_prefix}{key}"


# Global router instance
router = ModelRouter()


def get_router() -> ModelRouter:
    return router
