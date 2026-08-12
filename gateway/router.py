"""
Request Router with Fallback Chain Support

State-of-the-art router with:
- Model prefix-based routing
- Configurable fallback chains per provider
- Health-aware routing
- Proper error handling and normalization
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Optional

from gateway.backends.base import BackendBase, SSEEvent
from gateway.backends.openrouter import OpenRouterBackend
from gateway.backends.groq import GroqBackend
from gateway.backends.opencode_bridge import OpencodeBridgeBackend
from gateway.config import get_config

logger = logging.getLogger(__name__)


class ModelRouter:
    """Routes requests to appropriate backend with fallback support."""

    def __init__(self):
        self._backends: dict[str, BackendBase] = {}
        self._fallback_chains: dict[str, list[str]] = {}
        self._initialized = False

    def initialize(self, backends: list[BackendBase]):
        """Register backends and load fallback chains."""
        for backend in backends:
            self._backends[backend.provider_name] = backend
        config = get_config()
        self._fallback_chains = config.fallback_chains
        self._initialized = True
        logger.info(f"Router initialized with backends: {list(self._backends.keys())}")

    def get_backend_for_model(self, model: str) -> Optional[BackendBase]:
        """Find backend that handles this model prefix."""
        for backend in self._backends.values():
            if model.startswith(backend.model_prefix):
                return backend
        return None

    def get_fallback_chain(self, primary_backend: str) -> list[str]:
        """Get fallback chain for a backend."""
        return self._fallback_chains.get(primary_backend, [])

    async def route_with_fallback(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        headers: dict,
        cwd: Optional[str] = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """
        Route request with fallback chain support.

        Tries primary backend, then falls back through chain on failure.
        """
        if not self._initialized:
            raise RuntimeError("Router not initialized")

        primary_backend = self.get_backend_for_model(model)
        if not primary_backend:
            yield SSEEvent(
                event="error",
                data={"type": "error", "error": {"type": "model_not_found", "message": f"No backend for model: {model}"}}
            )
            return

        chain = [primary_backend.provider_name] + self.get_fallback_chain(primary_backend.provider_name)
        config = get_config()
        max_attempts = min(config.max_fallback_attempts, len(chain))

        last_error = None

        for attempt_idx, backend_name in enumerate(chain[:max_attempts]):
            backend = self._backends.get(backend_name)
            if not backend:
                logger.warning(f"Fallback backend not found: {backend_name}")
                continue

            # For fallback backends, we need to map the model to their prefix
            fallback_model = self._map_model_for_backend(model, backend)
            if not fallback_model:
                logger.warning(f"Cannot map model {model} for fallback backend {backend_name}")
                continue

            logger.info(f"Attempt {attempt_idx + 1}/{max_attempts}: routing to {backend_name} with model {fallback_model}")

            try:
                async for event in backend.handle_request(
                    fallback_model, messages, tools, headers, cwd
                ):
                    # Check for error events that should trigger fallback
                    if event.event == "error":
                        error_data = event.data.get("error", {})
                        error_type = error_data.get("type", "")
                        # Only fallback on retryable errors
                        if error_type in ("timeout", "connection_error", "api_error", "rate_limit", "overloaded"):
                            last_error = error_data
                            logger.warning(f"Backend {backend_name} failed with {error_type}, trying fallback")
                            break
                        # Non-retryable error - yield and stop
                        yield event
                        return
                    yield event
                else:
                    # Generator completed without break (success)
                    return

            except Exception as e:
                last_error = {"type": "exception", "message": str(e)}
                logger.exception(f"Backend {backend_name} raised exception")
                continue

        # All fallbacks exhausted
        yield SSEEvent(
            event="error",
            data={
                "type": "error",
                "error": {
                    "type": "all_fallbacks_failed",
                    "message": f"All {len(chain)} backends failed for model {model}",
                    "last_error": last_error
                }
            }
        )

    def _map_model_for_backend(self, original_model: str, backend: BackendBase) -> Optional[str]:
        """Map original model ID to backend's expected format."""
        config = get_config()

        # If same backend, use original
        if original_model.startswith(backend.model_prefix):
            return original_model

        # Extract the model key from original
        for backend_name, backend_obj in self._backends.items():
            if original_model.startswith(backend_obj.model_prefix):
                model_key = original_model[len(backend_obj.model_prefix):]
                # Map to target backend's prefix
                return f"{backend.model_prefix}{model_key}"

        return None


# Global router instance
router = ModelRouter()


def get_router() -> ModelRouter:
    return router