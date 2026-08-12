"""Soak test — sustained load against a generated project for 5 minutes.

Uses httpx.ASGITransport for zero-network-overhead testing. No Docker,
no subprocess, no port binding. The ASGI app is loaded in-process and
hammered with concurrent async requests.

Measures:
1. Latency — p50/p95/p99
2. Error rate — must stay below 1%
3. Throughput — req/s baseline

Run::

    PYTHONPATH=. python3 tests/test_soak.py
    PYTHONPATH=. python3 tests/test_soak.py --duration 300  # 5 min default
    PYTHONPATH=. python3 tests/test_soak.py --duration 30   # quick smoke
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("RATE_LIMITING_ENABLED", "false")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "soak-test-secret-key-must-be-32-chars!!!")
os.environ.setdefault("MFA_FERNET_KEY", "L7gvXDh2v6syV65J0-iwLQMTYbVavNXO2vuXgntcFBo=")
for k in list(os.environ):
    if "REDIS" in k:
        del os.environ[k]

# SLOs
# SLOs for ASGI in-process soak (10 concurrent coroutines, single event loop).
# The generated project has 7 middleware layers (correlation, CORS, security
# headers, body size, idempotency, request logging, SlowAPI) + structlog per
# request. With 10 concurrent tasks on one loop, p50 ≈ 80-120ms is expected.
# These SLOs are calibrated to detect REGRESSIONS (memory leak, handler hang,
# error accumulation), not to benchmark raw framework performance.
#
# LATENCY SLOs are env-tunable. The tight defaults (200/500/1000ms) enforce the
# aspirational SLA on an idle/dev box. On a shared CI runner under heavy CPU
# contention (load ~200), absolute latency is environment-dependent and would
# flake; CI overrides these with generous values that still catch a pathological
# multi-x blowup. The FUNCTIONAL gate below (error rate, throughput/min-requests)
# is STRICT and NOT tunable — a latency knob must never let a 5xx or a stalled
# event loop slip through.
SLO_P50_MS = int(os.environ.get("HUGR_SOAK_P50_MS", "200"))
SLO_P95_MS = int(os.environ.get("HUGR_SOAK_P95_MS", "500"))
SLO_P99_MS = int(os.environ.get("HUGR_SOAK_P99_MS", "1000"))
SLO_ERROR_RATE = 0.01

MODELS = {
    "Product": {"name": "str", "price": "float", "stock": "int"},
    "Order": {"status": "str", "total": "float"},
}

SOAK_TOOLS: list[tuple[str, str]] = [
    ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete"),
    ("add_cursor_pagination", "adapt.extend.crud_data.add_cursor_pagination"),
    ("add_bulk_operations", "adapt.extend.crud_data.add_bulk_operations"),
    ("add_data_export", "adapt.extend.crud_data.add_data_export"),
    ("add_file_upload", "adapt.extend.crud_data.add_file_upload"),
    ("add_circuit_breaker", "adapt.extend.infrastructure.add_circuit_breaker"),
    ("add_outbox_pattern", "adapt.extend.infrastructure.add_outbox_pattern"),
    ("add_saga", "adapt.extend.infrastructure.add_saga"),
    ("add_email_templates", "adapt.extend.infrastructure.add_email_templates"),
    ("add_api_versioning", "adapt.extend.api_design.add_api_versioning"),
    ("add_batch_endpoint", "adapt.extend.api_design.add_batch_endpoint"),
    ("add_graphql", "adapt.extend.api_design.add_graphql"),
    ("add_contract_tests", "adapt.extend.testing_tools.add_contract_tests"),
    ("add_factory", "adapt.extend.testing_tools.add_factory"),
    ("add_load_profile", "adapt.extend.testing_tools.add_load_profile"),
]


def _generate_project(tmp_dir: Path) -> Path:
    from adapt.contracts import ToolInput
    from tests.common.fixture_factory import create_fixture_project

    project_dir = create_fixture_project(
        name="soak_project",
        models=MODELS,
        tmp_dir=tmp_dir,
    )

    applied = 0
    for name, mod_path in SOAK_TOOLS:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, name)
        r = fn(ToolInput(project_dir=str(project_dir)))
        if r.status in ("success", "no_op"):
            applied += 1

    print(f"  [setup] Applied {applied}/{len(SOAK_TOOLS)} tools")

    # Patch db.py for SQLite (no Docker dependency)
    db_file = project_dir / "app" / "core" / "db.py"
    if db_file.exists():
        sqlite_db = project_dir / "soak.db"
        db_file.write_text(
            '"""Database engine — patched for soak test (SQLite)."""\n\n'
            "from sqlalchemy.ext.asyncio import create_async_engine\n\n"
            f'engine = create_async_engine("sqlite+aiosqlite:///{sqlite_db}",'
            " echo=False, future=True)\n\n"
            "async def init_db() -> None:\n"
            "    from app.models.base import Base\n"
            "    import app.models\n"
            "    async with engine.begin() as conn:\n"
            "        await conn.run_sync(Base.metadata.create_all)\n"
        )

    # Patch IdempotencyMiddleware to no-op (no Redis)
    idem_file = project_dir / "app" / "middleware" / "idempotency.py"
    if idem_file.exists():
        idem_file.write_text(
            '"""Idempotency middleware — soak test stub (no Redis)."""\n\n'
            "from starlette.middleware.base import BaseHTTPMiddleware\n"
            "from starlette.requests import Request\n"
            "from starlette.responses import Response\n\n"
            "class IdempotencyMiddleware(BaseHTTPMiddleware):\n"
            "    async def dispatch(self, request: Request, call_next) -> Response:\n"
            "        return await call_next(request)\n"
        )

    return project_dir


def _load_app(project_dir: Path):
    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    return importlib.import_module("app.main").app


async def _run_load(
    app,
    duration_seconds: int,
    concurrency: int = 10,
) -> dict:
    import httpx

    latencies: list[float] = []
    errors = 0
    status_codes: dict[int, int] = {}
    total = 0
    lock = asyncio.Lock()

    deadline = time.monotonic() + duration_seconds

    async def worker(client: httpx.AsyncClient, worker_id: int) -> None:
        nonlocal errors, total
        while time.monotonic() < deadline:
            t0 = time.monotonic()
            try:
                r = await client.get("http://test/healthz", timeout=5)
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with lock:
                    latencies.append(elapsed_ms)
                    status_codes[r.status_code] = status_codes.get(r.status_code, 0) + 1
                    if r.status_code >= 500:
                        errors += 1
                    total += 1
            except Exception:
                async with lock:
                    errors += 1
                    latencies.append((time.monotonic() - t0) * 1000)
                    total += 1

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workers = [asyncio.create_task(worker(client, i)) for i in range(concurrency)]
        await asyncio.gather(*workers)

    return {
        "total_requests": total,
        "errors": errors,
        "latencies_ms": sorted(latencies),
        "status_codes": status_codes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    print(f"\n{'=' * 70}")
    print(f"SOAK TEST — {args.duration}s at {args.concurrency} concurrent clients")
    print("(ASGI in-process transport — no Docker, no subprocess)")
    print(f"{'=' * 70}\n")

    # 1. Generate project
    print("[1/3] Generating project with tools...")
    tmp = tempfile.mkdtemp()
    project_dir = _generate_project(Path(tmp))

    # 2. Load app
    print("[2/3] Loading app...")
    app = _load_app(project_dir)
    print(f"  App loaded from {project_dir.name}")

    # 3. Run load
    print(f"[3/3] Running {args.duration}s soak ({args.concurrency} clients)...")
    t0 = time.monotonic()
    result = asyncio.run(_run_load(app, args.duration, args.concurrency))
    elapsed = time.monotonic() - t0

    lats = result["latencies_ms"]
    total = result["total_requests"]
    errs = result["errors"]

    if not lats:
        print("  FAIL: No requests completed")
        return 1

    p50 = statistics.median(lats)
    p95 = lats[int(len(lats) * 0.95)] if len(lats) > 20 else max(lats)
    p99 = lats[int(len(lats) * 0.99)] if len(lats) > 100 else max(lats)
    error_rate = errs / total if total else 1.0
    rps = total / elapsed if elapsed else 0

    print(f"\n  Requests:    {total:,} ({rps:.0f} req/s)")
    print(f"  Errors:      {errs} ({error_rate * 100:.2f}%)")
    print(f"  Latency p50: {p50:.1f}ms")
    print(f"  Latency p95: {p95:.1f}ms")
    print(f"  Latency p99: {p99:.1f}ms")
    print(f"  Status codes: {result['status_codes']}")

    # STRICT functional floor (NOT env-tunable): even a fully saturated runner
    # must sustain at least 1 req/s/client. A stalled event loop or hung handler
    # would crater throughput below this — the latency knob must never mask it.
    min_requests = args.duration * args.concurrency

    failures = []
    if p50 > SLO_P50_MS:
        failures.append(f"p50 {p50:.0f}ms > SLO {SLO_P50_MS}ms")
    if p95 > SLO_P95_MS:
        failures.append(f"p95 {p95:.0f}ms > SLO {SLO_P95_MS}ms")
    if p99 > SLO_P99_MS:
        failures.append(f"p99 {p99:.0f}ms > SLO {SLO_P99_MS}ms")
    if error_rate > SLO_ERROR_RATE:
        failures.append(f"error rate {error_rate * 100:.1f}% > SLO {SLO_ERROR_RATE * 100:.0f}%")
    if total < min_requests:
        failures.append(
            f"throughput {total} req < floor {min_requests} "
            f"({args.concurrency} clients x {args.duration}s x 1 req/s)"
        )

    print(f"\n{'=' * 70}")
    if failures:
        print(f"SOAK TEST: FAILED — {len(failures)} SLO violation(s)")
        for f in failures:
            print(f"  - {f}")
    else:
        print("SOAK TEST: PASSED — all SLOs met")
        print(
            f"  {total:,} requests over {elapsed:.0f}s, {rps:.0f} req/s, "
            f"p99={p99:.0f}ms, {errs} errors"
        )
    print(f"{'=' * 70}\n")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
