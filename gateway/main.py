"""
Claude Code Gateway - Main FastAPI Application

State-of-the-art gateway with:
- Anthropic Messages API compatibility
- Gateway model discovery
- Multi-backend routing with fallback
- Structured logging
- Health monitoring
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from gateway.backends.base import SSEEvent
from gateway.backends.openrouter import OpenRouterBackend
from gateway.backends.groq import GroqBackend
from gateway.backends.gemini import GeminiBackend
from gateway.backends.nim import NimBackend
from gateway.backends.mistral import MistralBackend
from gateway.backends.opencode_bridge import OpencodeBridgeBackend
from gateway.config import config, get_config
from gateway.discovery import get_discovery_payload
from gateway.health import check_all_backends, HealthResponse
from gateway.router import router, get_router

# Configure structlog
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - startup and shutdown."""
    logger.info("claude-gateway starting up")

    # Initialize config
    cfg = get_config()
    logger.info("config loaded", port=cfg.port, log_level=cfg.log_level)

    # Initialize backends
    backends = [
        OpenRouterBackend(),
        GroqBackend(),
        GeminiBackend(),
        NimBackend(),
        MistralBackend(),
        OpencodeBridgeBackend(),
    ]

    # Initialize router with backends
    get_router().initialize(backends)

    # Store backends in app state for shutdown
    app.state.backends = backends

    logger.info("all backends initialized", backends=[b.provider_name for b in backends])

    yield

    # Shutdown
    logger.info("claude-gateway shutting down")
    for backend in backends:
        try:
            await backend.close()
        except Exception as e:
            logger.warning("error closing backend", backend=backend.name, error=str(e))


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="Claude Code Gateway",
        version="0.1.0",
        description="Multi-provider gateway for Claude Code native model picker",
        lifespan=lifespan,
    )

    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        structlog.contextvars.bind_contextvars(
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
        )
        response = await call_next(request)
        logger.info("request completed", status_code=response.status_code)
        return response

    # Routes
    @app.get("/v1/models")
    async def list_models():
        """Model discovery endpoint for Claude Code."""
        cfg = get_config()
        payload = await get_discovery_payload(cfg)
        return Response(
            content=payload,
            media_type="application/json",
        )

    @app.post("/v1/messages")
    async def messages(request: Request):
        """Anthropic Messages API endpoint with routing and fallback."""
        try:
            body = await request.json()
        except Exception:
            return Response(
                content='{"type":"error","error":{"type":"invalid_request","message":"Invalid JSON"}}',
                media_type="application/json",
                status_code=400,
            )

        model = body.get("model")
        messages = body.get("messages", [])
        tools = body.get("tools")
        headers = dict(request.headers)

        if not model:
            return Response(
                content='{"type":"error","error":{"type":"invalid_request","message":"model is required"}}',
                media_type="application/json",
                status_code=400,
            )

        # Route through router with fallback
        router_instance = get_router()

        async def event_stream():
            async for event in router_instance.route_with_fallback(
                model, messages, tools, headers, cwd=None
            ):
                # Handle raw passthrough (OpenRouter)
                if event.event == "raw":
                    yield event.data["line"] + "\n"
                elif event.event:
                    yield f"event: {event.event}\n"
                    if event.data is not None:
                        import orjson
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
            }
        )

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        backends = getattr(globals().get("app"), "state", {}).get("backends", [])
        if not backends:
            # Fallback: create fresh instances
            from gateway.backends.openrouter import OpenRouterBackend
            from gateway.backends.groq import GroqBackend
            from gateway.backends.gemini import GeminiBackend
            from gateway.backends.nim import NimBackend
            from gateway.backends.mistral import MistralBackend
            from gateway.backends.opencode_bridge import OpencodeBridgeBackend

            backends = [
                OpenRouterBackend(),
                GroqBackend(),
                GeminiBackend(),
                NimBackend(),
                MistralBackend(),
                OpencodeBridgeBackend(),
            ]

        health_response = await check_all_backends(backends)
        status_code = 200 if health_response.status == "healthy" else 503
        return Response(
            content=health_response.to_dict(),
            media_type="application/json",
            status_code=status_code,
        )

    @app.get("/")
    async def root():
        """Root endpoint with basic info."""
        return {
            "name": "Claude Code Gateway",
            "version": "0.1.0",
            "endpoints": {
                "models": "/v1/models",
                "messages": "/v1/messages",
                "health": "/health",
            }
        }

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "gateway.main:app",
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        reload=False,
    )