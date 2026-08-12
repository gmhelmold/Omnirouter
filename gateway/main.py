"""
Claude Code Gateway - main FastAPI application.

- Anthropic Messages API compatibility (streaming and non-streaming).
- Gateway model discovery (/v1/models).
- Multi-backend routing with fallback.
- Structured logging and health monitoring.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import orjson
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from gateway.backends.anthropic import AnthropicBackend
from gateway.backends.base import BackendBase
from gateway.backends.cerebras import CerebrasBackend
from gateway.backends.gemini import GeminiBackend
from gateway.backends.groq import GroqBackend
from gateway.backends.mistral import MistralBackend
from gateway.backends.nim import NimBackend
from gateway.backends.opencode_bridge import OpencodeBridgeBackend
from gateway.backends.openrouter import OpenRouterBackend
from gateway.config import get_config
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
        CerebrasBackend(),
        OpencodeBridgeBackend(),
        # Catch-all LAST: native Anthropic models (claude-opus-*, claude-sonnet-*, ...)
        # pass through to api.anthropic.com using the client's own credential.
        AnthropicBackend(),
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


# Map an Anthropic-style error type to the right HTTP status. Free-tier engines
# fail mostly with rate_limit/quota — surfacing that as 429 (not a blanket 502)
# lets clients back off and retry instead of treating it as a gateway crash.
_ERROR_STATUS = {
    "rate_limit": 429,
    "overloaded": 503,
    "timeout": 504,
    "auth_error": 401,
    "authentication_error": 401,
    "permission_error": 403,
    "invalid_request": 400,
    "invalid_request_error": 400,
    "model_not_found": 404,
    "not_found": 404,
}


def _http_status_for_error(error: dict[str, Any]) -> int:
    """Pick an HTTP status from an error payload's type.

    For ``all_fallbacks_failed`` the meaningful cause is the wrapped
    ``last_error`` (e.g. every backend rate-limited), so unwrap it first.
    Anything unrecognised (connection_error, api_error, exception) stays 502.
    """
    error_type = error.get("type", "")
    if error_type == "all_fallbacks_failed" and isinstance(error.get("last_error"), dict):
        error_type = error["last_error"].get("type", "")
    return _ERROR_STATUS.get(error_type, 502)


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
    async def list_models(request: Request) -> Response:
        """Model discovery endpoint for Claude Code.

        Pass ``?free=1`` to list only the free-tagged models (handy for
        eyeballing what costs nothing); Claude Code itself calls it unfiltered.
        """
        from gateway.config import get_config

        payload = await get_discovery_payload(get_config())
        if request.query_params.get("free") in ("1", "true", "yes"):
            data = [m for m in payload.get("data", []) if "FREE 🆓" in m.get("display_name", "")]
            payload = {"data": data}
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
        logger.info("incoming request model=%s stream=%s", model, stream)

        if not stream:
            events = []
            async for event in router_instance.route_with_fallback(model, body, headers, cwd=None):
                if event.event == "error":
                    status = _http_status_for_error(event.data.get("error", {}))
                    return _json_response(event.data, status_code=status)
                events.append(event)
            return _json_response(collect_message(events, model))

        # Passive keepalive: some models (e.g. large reasoning models) take tens
        # of seconds before the first token. While the upstream is silent we
        # emit an Anthropic `ping` every keepalive_interval seconds so the
        # client/orchestrator knows the request is still alive and keeps waiting
        # instead of treating the idle stream as dead. It never cancels the
        # in-flight request — the same pending event is awaited across pings.
        keepalive = get_config().keepalive_interval

        async def event_stream() -> AsyncGenerator[str, None]:
            agen = router_instance.route_with_fallback(model, body, headers, cwd=None)
            ait = agen.__aiter__()
            pending: asyncio.Task[Any] = asyncio.ensure_future(ait.__anext__())
            try:
                while True:
                    if keepalive and keepalive > 0:
                        done, _ = await asyncio.wait({pending}, timeout=keepalive)
                        if not done:
                            # Upstream still working — heartbeat, don't kill.
                            yield "event: ping\n"
                            yield 'data: {"type": "ping"}\n\n'
                            continue
                    else:
                        await asyncio.wait({pending})

                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        break

                    if event.event == "raw":
                        yield event.data["line"] + "\n"
                    elif event.event:
                        yield f"event: {event.event}\n"
                        if event.data is not None:
                            yield f"data: {orjson.dumps(event.data).decode()}\n"
                        if event.retry:
                            yield f"retry: {event.retry}\n"
                        yield "\n"

                    pending = asyncio.ensure_future(ait.__anext__())
            finally:
                if not pending.done():
                    pending.cancel()
                await agen.aclose()

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
