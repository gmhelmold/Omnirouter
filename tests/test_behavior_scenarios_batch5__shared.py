"""Shared framework for BATCH 5 behavior scenarios (split from
``test_behavior_scenarios_batch5.py``).

Contains env setup, SQLite/idempotency patch templates, the Scenario /
ScenarioContext dataclasses, all shared helpers and the ``_run_scenario``
driver. The scenario definitions + parametrized pytest entrypoints live in
the sibling ``test_behavior_scenarios_batch5*.py`` files.

These tests intentionally do NOT require PostgreSQL — they use SQLite so
that CI can run them with zero external infrastructure.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import textwrap
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Env setup — must happen before any app import
# ---------------------------------------------------------------------------

os.environ.setdefault("RATE_LIMITING_ENABLED", "false")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "behavior-batch5-secret-key-32+chars-ok!")
os.environ.setdefault("MFA_FERNET_KEY", "L7gvXDh2v6syV65J0-iwLQMTYbVavNXO2vuXgntcFBo=")
os.environ.pop("REDIS_URL", None)  # no Redis in these tests

import asyncio

import pytest

# ---------------------------------------------------------------------------
# Patch templates (SQLite + idempotency stub)
# ---------------------------------------------------------------------------

_SQLITE_DB_PY = textwrap.dedent("""\
    \"\"\"Patched db.py — SQLite+aiosqlite, no Docker required.\"\"\"

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )


    async def init_db() -> None:
        \"\"\"Run startup checks.\"\"\"
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))


    __all__ = ["engine", "init_db"]
""")

_PASSTHROUGH_IDEMPOTENCY_PY = textwrap.dedent("""\
    \"\"\"Patched idempotency.py — pass-through stub (no Redis).\"\"\"

    from __future__ import annotations
    from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response


    class IdempotencyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
            return await call_next(request)
""")


# ---------------------------------------------------------------------------
# Scenario framework
# ---------------------------------------------------------------------------

ScenarioFlow = Callable[["ScenarioContext"], Awaitable[None]]


def _import_project_module(project_dir: Path, dotted: str):
    """Import a ``core.venous...`` module from the *generated project* copy.

    Behavior scenarios assert against what each tool ACTUALLY ships, so we load
    the project's own copied primitive/adapter (not the Arsenal source) by
    putting the project dir first on ``sys.path`` and flushing stale modules.
    """
    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)
    for m in list(sys.modules):
        if m == "core" or m.startswith("core."):
            del sys.modules[m]
    return importlib.import_module(dotted)


@dataclass
class Scenario:
    name: str
    archetype: str
    models: dict[str, dict[str, str]]
    tools: list[tuple[str, str]]  # (tool_name, module_path)
    flow: ScenarioFlow
    needs_boot: bool = True  # Set False for file-only scenarios (no HTTP)


@dataclass
class ScenarioContext:
    client: object  # httpx.AsyncClient
    session: object  # AsyncSession
    engine: object  # AsyncEngine
    project_dir: Path
    report_section: list[tuple[str, bool, str]] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.report_section.append((name, ok, detail))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _patch_project(project_dir: Path) -> None:
    """Overwrite db.py with SQLite engine and idempotency with pass-through stub."""
    (project_dir / "app" / "core" / "db.py").write_text(_SQLITE_DB_PY)
    idempotency_path = project_dir / "app" / "middleware" / "idempotency.py"
    if idempotency_path.exists():
        idempotency_path.write_text(_PASSTHROUGH_IDEMPOTENCY_PY)


def _load_app(project_dir: Path):
    key = str(project_dir)
    sys.path[:] = [p for p in sys.path if not (p != key and Path(p, "app").is_dir())]
    if key not in sys.path:
        sys.path.insert(0, key)
    for m in list(sys.modules):
        if m == "app" or m.startswith("app."):
            del sys.modules[m]
    importlib.import_module("app.models")
    return importlib.import_module("app.main").app


async def _make_client(project_dir: Path):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    app = _load_app(project_dir)
    get_session_mod = importlib.import_module("app.core.session")
    base_mod = importlib.import_module("app.models.base")

    engine = create_async_engine("sqlite+aiosqlite://", echo=False, future=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(base_mod.Base.metadata.create_all)

    session = factory()

    async def _override():
        yield session

    app.dependency_overrides[get_session_mod.get_session] = _override
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, session, engine


async def _teardown(app, client, session, engine) -> None:
    await client.aclose()
    await session.close()
    await engine.dispose()
    app.dependency_overrides.clear()


def _th(token: str | None) -> dict[str, str]:
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


async def _signup(client, email: str, pwd: str, name: str) -> str:
    r = await client.post(
        "/api/v1/users/signup",
        json={
            "email": email,
            "password": pwd,
            "full_name": name,
        },
    )
    assert r.status_code in (200, 201), f"signup {email}: {r.status_code} {r.text[:300]}"
    r = await client.post(
        "/api/v1/login/access-token",
        data={
            "username": email,
            "password": pwd,
        },
    )
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:300]}"
    return r.json()["access_token"]


def _build_project(scenario: Scenario, tmp: Path) -> Path:
    from adapt.contracts import ToolInput
    from tests.common.fixture_factory import create_fixture_project

    project_dir = create_fixture_project(
        name=f"scn_{scenario.name}",
        models=scenario.models,
        tmp_dir=tmp,
    )
    _patch_project(project_dir)

    for tool_name, mod_path in scenario.tools:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, tool_name)
        result = fn(ToolInput(project_dir=str(project_dir)))
        if result.status == "error":
            raise RuntimeError(f"{tool_name}: {result.error}")

    return project_dir


async def _run_scenario(scenario: Scenario) -> tuple[int, int, list[tuple[str, bool, str]]]:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            project_dir = _build_project(scenario, Path(tmp))
        except Exception as exc:
            return 0, 1, [("generate_and_apply", False, f"{type(exc).__name__}: {str(exc)[:300]}")]

        if not scenario.needs_boot:
            ctx = ScenarioContext(
                client=None,
                session=None,
                engine=None,
                project_dir=project_dir,
            )
            try:
                await scenario.flow(ctx)
            except Exception as exc:
                ctx.record("flow", False, f"EXCEPTION: {type(exc).__name__}: {str(exc)[:300]}")
                traceback.print_exc()
            passed = sum(1 for _, ok, _ in ctx.report_section if ok)
            total = len(ctx.report_section)
            return passed, total, ctx.report_section

        try:
            app, client, session, engine = await _make_client(project_dir)
        except Exception as exc:
            return 0, 1, [("boot", False, f"{type(exc).__name__}: {str(exc)[:300]}")]

        ctx = ScenarioContext(
            client=client,
            session=session,
            engine=engine,
            project_dir=project_dir,
        )
        try:
            await scenario.flow(ctx)
        except Exception as exc:
            ctx.record("flow", False, f"EXCEPTION: {type(exc).__name__}: {str(exc)[:300]}")
            traceback.print_exc()
        finally:
            await _teardown(app, client, session, engine)

    passed = sum(1 for _, ok, _ in ctx.report_section if ok)
    total = len(ctx.report_section)
    return passed, total, ctx.report_section


# ---------------------------------------------------------------------------
# Shared pytest entrypoint helper
# ---------------------------------------------------------------------------


async def run_scenario_assert(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    passed, total, details = await _run_scenario(scenario)

    failures = [(n, d) for n, ok, d in details if not ok]
    if failures:
        lines = [f"\n  SCENARIO: {scenario.name} ({scenario.archetype})"]
        for n, d in failures:
            lines.append(f"    [FAIL] {n}: {d}")
        pytest.fail("\n".join(lines) + f"\n  → {passed}/{total} assertions passed")
