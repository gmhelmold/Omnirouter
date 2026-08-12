"""
Health Check Module

Aggregates health status from all backends.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from gateway.backends.base import BackendBase, BackendHealth


@dataclass
class HealthResponse:
    """Aggregated health response."""
    status: str  # "healthy", "degraded", "unhealthy"
    backends: dict[str, BackendHealth]
    timestamp: float = field(default_factory=lambda: __import__('time').time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "timestamp": self.timestamp,
            "backends": {
                name: {
                    "healthy": h.healthy,
                    "latency_ms": h.latency_ms,
                    "error": h.error,
                    "checked_at": h.checked_at,
                }
                for name, h in self.backends.items()
            }
        }


async def check_all_backends(backends: list[BackendBase]) -> HealthResponse:
    """
    Run health checks on all backends in parallel.

    Returns aggregated health response.
    """
    # Run all health checks concurrently
    health_results = await asyncio.gather(
        *[backend.health_check() for backend in backends],
        return_exceptions=True
    )

    backend_health = {}
    healthy_count = 0
    total_count = len(backends)

    for backend, result in zip(backends, health_results):
        if isinstance(result, Exception):
            backend_health[backend.provider_name] = BackendHealth(
                name=backend.name,
                healthy=False,
                error=f"Health check exception: {result}"
            )
        elif isinstance(result, BackendHealth):
            backend_health[backend.provider_name] = result
            if result.healthy:
                healthy_count += 1
        else:
            backend_health[backend.provider_name] = BackendHealth(
                name=backend.name,
                healthy=False,
                error="Unknown health check result"
            )

    # Determine overall status
    if healthy_count == total_count:
        overall = "healthy"
    elif healthy_count > 0:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return HealthResponse(
        status=overall,
        backends=backend_health,
    )


async def quick_health_check(backends: list[BackendBase]) -> dict[str, bool]:
    """Quick check - returns just healthy/unhealthy per backend."""
    results = await asyncio.gather(
        *[backend.health_check() for backend in backends],
        return_exceptions=True
    )
    return {
        backend.provider_name: (
            isinstance(r, BackendHealth) and r.healthy
        )
        for backend, r in zip(backends, results)
    }