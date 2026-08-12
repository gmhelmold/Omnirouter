"""
Claude Code Gateway - main FastAPI application.

- Anthropic Messages API compatibility (streaming and non-streaming).
- Gateway model discovery (/v1/models).
- Multi-backend routing with fallback.
- Structured logging and health monitoring.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import orjson
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from gateway.backends.base import BackendBase
from gateway.backends.gemini import GeminiBackend
from gateway.backends.groq import GroqBackend
from gateway.backends.mistral import MistralBackend
from gateway.backends.nim import NimBackend
from gateway.backends.opencode_bridge import OpencodeBridgeBackend
from gateway.backends.openrouter import OpenRouterBackend
from gateway.discovery import get_discovery_payload
from gateway.health import check_all_backends
from gateway.messages import collect_message
from gateway.router import get_router

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def _build_backends() -> list[BackendBase]:
    return [
        OpenRouterBackend(),
        GroqBackend(),
        GeminiBackend(),
        NimBackend(),
        MistralBackend(),
        OpencodeBridgeBackend(),
    ]


def _json_response(payload: object, status_code: int = 200) -> Response:
    return Response(
        content=orjson.dumps(payload),
        media_type="application/json",
        status_code=status_code,
    )


def _error_response(error_type: str, message: str, status_code: int) -> Response:
    return _json_response(
        {"type": "error", "error": {"type": error_type, "message": message}},
        status_code=status_code,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - startup and shutdown."""
    from gateway.config import get_config

    cfg = get_config()
    logger.info("claude-gateway starting up", port=cfg.port, log_level=cfg.log_level)

    backends = _build_backends()
    get_router().initialize(backends)
    app.state.backends = backends
    logger.info("all backends initialized", backends=[b.provider_name for b in backends])

    yield

    logger.info("claude-gateway shutting down")
    for backend in backends:
        try:
            await backend.close()
        except Exception as exc:
            logger.warning("error closing backend", backend=backend.name, error=str(exc))


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Claude Code Gateway",
        version="0.1.0",
        description="Multi-provider gateway for Claude Code native model picker",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        structlog.contextvars.bind_contextvars(
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
        )
        response = await call_next(request)
        logger.info("request completed", status_code=response.status_code)
        return response

    @app.get("/v1/models")
    async def list_models() -> Response:
        """Model discovery endpoint for Claude Code."""
        from gateway.config import get_config

        payload = await get_discovery_payload(get_config())
        return _json_response(payload)

    @app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        """Anthropic Messages API endpoint with routing and fallback."""
        try:
            body = await request.json()
        except Exception:
            return _error_response("invalid_request", "Invalid JSON", 400)

        if not isinstance(body, dict):
            return _error_response("invalid_request", "Request body must be an object", 400)

        model = body.get("model")
        if not model:
            return _error_response("invalid_request", "model is required", 400)

        headers = dict(request.headers)
        router_instance = get_router()
        stream = body.get("stream", True)

        if not stream:
            events = []
            async for event in router_instance.route_with_fallback(model, body, headers, cwd=None):
                if event.event == "error":
                    return _json_response(event.data, status_code=502)
                events.append(event)
            return _json_response(collect_message(events, model))

        async def event_stream() -> AsyncGenerator[str, None]:
            async for event in router_instance.route_with_fallback(model, body, headers, cwd=None):
                if event.event == "raw":
                    yield event.data["line"] + "\n"
                elif event.event:
                    yield f"event: {event.event}\n"
                    if event.data is not None:
                        yield f"data: {orjson.dumps(event.data).decode()}\n"
                    if event.retry:
                        yield f"retry: {event.retry}\n"
                    yield "\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/health")
    async def health(request: Request) -> Response:
        """Health check endpoint."""
        backends = getattr(request.app.state, "backends", None) or _build_backends()
        health_response = await check_all_backends(backends)
        status_code = 200 if health_response.status == "healthy" else 503
        return _json_response(health_response.to_dict(), status_code=status_code)

    @app.get("/")
    async def root() -> dict[str, Any]:
        """Root endpoint with basic info."""
        return {
            "name": "Claude Code Gateway",
            "version": "0.1.0",
            "endpoints": {
                "models": "/v1/models",
                "messages": "/v1/messages",
                "health": "/health",
            },
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from gateway.config import get_config

    cfg = get_config()
    uvicorn.run(
        "gateway.main:app",
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level.lower(),
        reload=False,
    )
