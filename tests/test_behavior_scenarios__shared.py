"""BEHAVIOR scenarios — shared framework + helpers.

This module is the split-out shared foundation for the behavior-scenario
harness (see test_behavior_scenarios.py). It carries the environment
setup, the Scenario / ScenarioContext dataclasses, and every shared
helper. It is imported FIRST by both the scenario part files and the
runner so that the os.environ.setdefault(...) calls run before any
application module is imported.

Requires PostgreSQL 16 on localhost:54329 (see test_e2e_postgres.py).
"""

from __future__ import annotations

import os

os.environ.setdefault("RATE_LIMITING_ENABLED", "false")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "behavior-scenarios-secret-key-32+chars-ok!")
os.environ.setdefault("POSTGRES_SERVER", "localhost")
os.environ.setdefault("POSTGRES_PORT", "54329")
os.environ.setdefault("POSTGRES_USER", "skill")
os.environ.setdefault("POSTGRES_PASSWORD", "skill")
os.environ.setdefault("POSTGRES_DB", "skill_e2e")
os.environ.setdefault("MFA_FERNET_KEY", "L7gvXDh2v6syV65J0-iwLQMTYbVavNXO2vuXgntcFBo=")

import importlib
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Scenario framework
# ---------------------------------------------------------------------------

ScenarioFlow = Callable[["ScenarioContext"], Awaitable[list[tuple[str, bool, str]]]]


@dataclass
class Scenario:
    """A domain scenario: models + tools + behavior assertions."""

    name: str
    archetype: str  # Short description of the domain archetype
    models: dict[str, dict[str, str]]
    tools: list[tuple[str, str]]  # (tool_name, module_path)
    flow: ScenarioFlow
    tenant_slug: str = "acme"
    needs_multi_tenancy: bool = True


@dataclass
class ScenarioContext:
    """Runtime context passed to every scenario flow."""

    client: object  # httpx.AsyncClient
    session: object  # AsyncSession
    engine: object  # AsyncEngine
    project_dir: Path
    tenant_slug: str
    report_section: list[tuple[str, bool, str]] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.report_section.append((name, ok, detail))


# ---------------------------------------------------------------------------
# Shared helpers (copied from test_e2e_postgres.py patterns)
# ---------------------------------------------------------------------------

# Honour E2E_POSTGRES_URL like test_e2e_postgres.py so the same DB works for
# both harnesses; default to the documented `docker run` instance on 54329.
# (Previously hardcoded to 54329, which silently SKIPped whenever Postgres was
# reachable on a different port — including CI's 5432 service.)
POSTGRES_URL = os.environ.get(
    "E2E_POSTGRES_URL",
    "postgresql+asyncpg://skill:skill@localhost:54329/skill_e2e",
)


async def _precheck_postgres() -> bool:
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        e = create_async_engine(POSTGRES_URL, echo=False)
        async with e.connect() as c:
            await c.execute(text("SELECT 1"))
        await e.dispose()
        return True
    except Exception:
        return False


async def _reset_schema(engine) -> None:
    from sqlalchemy import text

    async with engine.begin() as c:
        await c.execute(text("DROP SCHEMA public CASCADE"))
        await c.execute(text("CREATE SCHEMA public"))


def _tool_deliverable(module: str, symbol: str) -> tuple[bool, str]:
    """Return (present, detail) for a tool's emitted wiring helper.

    The infra tools (rbac/audit/saga/mfa/webhook-receiver) were refactored from
    DB-table generators into thin primitive-backed wiring helpers, so their
    behaviour is verified by the helper module + symbol being importable and
    callable — not by a (no-longer-created) table name.
    """
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError:
        return False, f"{module} not generated"
    fn = getattr(mod, symbol, None)
    return callable(fn), f"{module}.{symbol} {'present' if callable(fn) else 'MISSING'}"


def _load_app(project_dir: Path):
    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)
    # Purge BOTH app.* and core.* before importing. Each scenario scaffolds its
    # own project (with only the core.venous adapters its tools copied in) onto
    # sys.path. Purging only app.* leaves the previous scenario's core.venous
    # pinned in sys.modules — so a later scenario importing an adapter that
    # project did copy (e.g. AuditLogAdapter / WorkflowAdapter) hits
    # ModuleNotFoundError. Dropping core.* too forces a fresh resolve per
    # scenario from this project's tree.
    for m in list(sys.modules):
        if m in ("app", "core") or m.startswith("app.") or m.startswith("core."):
            del sys.modules[m]
    return importlib.import_module("app.main").app


async def _make_client(project_dir: Path, tenant_slug: str, needs_multi_tenancy: bool):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    app = _load_app(project_dir)
    # Importing app.models triggers every model file registration via
    # the __init__.py imports — without this, Base.metadata would only
    # contain tables whose models happen to be imported transitively
    # through app.main (e.g. the scaffold user/tenant models but NOT
    # the tool-added outbox/saga/feature_flag models).
    importlib.import_module("app.models")
    base_mod = importlib.import_module("app.models.base")
    get_session_mod = importlib.import_module("app.core.session")

    # NullPool: every operation opens a fresh connection. This avoids
    # stale pool connections carrying over cached schema state across
    # scenarios run in the same Python process.
    engine = create_async_engine(POSTGRES_URL, echo=False, future=True, poolclass=NullPool)
    await _reset_schema(engine)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as c:
        await c.run_sync(base_mod.Base.metadata.create_all)

    # Add default partition for audit_logs (if the table was created by
    # add_audit_log). Run in a SEPARATE transaction so failure does not
    # roll back the create_all transaction above.
    if "audit_logs" in base_mod.Base.metadata.tables:
        try:
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS audit_logs_default "
                        "PARTITION OF audit_logs DEFAULT"
                    )
                )
        except Exception:
            pass

    session = factory()

    if needs_multi_tenancy:
        try:
            tenant_mod = importlib.import_module("app.models.tenant")
            tid = uuid.uuid4()
            session.add(
                tenant_mod.Tenant(
                    id=tid,
                    name=tenant_slug.upper(),
                    slug=tenant_slug,
                    status="active",
                    allow_public_signup=True,
                )
            )
            await session.commit()
        except ModuleNotFoundError:
            pass

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


def _th(token: str | None, tenant: str = "acme") -> dict[str, str]:
    h = {"X-Tenant-ID": tenant}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _signup(client, email: str, pwd: str, name: str, tenant: str) -> str:
    r = await client.post(
        "/api/v1/users/signup",
        json={
            "email": email,
            "password": pwd,
            "full_name": name,
        },
        headers=_th(None, tenant),
    )
    assert r.status_code in (200, 201), f"signup {email}: {r.status_code} {r.text[:200]}"
    r = await client.post(
        "/api/v1/login/access-token",
        data={
            "username": email,
            "password": pwd,
        },
        headers=_th(None, tenant),
    )
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


async def _promote_superuser(session, email: str) -> None:
    from sqlalchemy import text

    await session.execute(
        text("UPDATE users SET is_superuser = true WHERE email = :e"),
        {"e": email},
    )
    await session.commit()
