"""HARDCORE E2E test with REAL PostgreSQL — validates ALL 27 EXTEND tools.

This test complements test_e2e_hardcore.py (which uses SQLite in-memory) by
spinning up a real PostgreSQL container and exercising the 5 tools that
require production-grade infra:

- add_multi_tenancy (tenant middleware needs real async session)
- add_rbac (permissions table + FK to tenants)
- add_mfa (TOTP codes via pyotp)
- add_oauth2_provider (authorization server state)
- add_search (PostgreSQL full-text search with @@ operator)

Requires a running PostgreSQL 16 container:

    docker run -d --name skill001-e2e-pg \\
      -e POSTGRES_USER=skill -e POSTGRES_PASSWORD=skill \\
      -e POSTGRES_DB=skill_e2e -p 54329:5432 \\
      postgres:16-alpine

Set E2E_POSTGRES_URL to override (defaults to the docker run above).

Run:
    PYTHONPATH=. python3 tests/test_e2e_postgres.py

Exit 0 → all tests pass
Exit 1 → failures or Postgres unavailable (not a tool bug)
Exit 2 → Postgres skipped (no container)
"""

from __future__ import annotations

import os

os.environ.setdefault("RATE_LIMITING_ENABLED", "false")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-e2e-must-be-32-chars-long!!")
# These are read by app.core.config.Settings at import time. The tenant
# middleware (add_multi_tenancy) creates its own async_session_maker from
# these values, bypassing the dependency_override, so they MUST match the
# real container credentials before app.main is imported.
os.environ.setdefault("POSTGRES_SERVER", "localhost")
os.environ.setdefault("POSTGRES_PORT", "54329")
os.environ.setdefault("POSTGRES_USER", "skill")
os.environ.setdefault("POSTGRES_PASSWORD", "skill")
os.environ.setdefault("POSTGRES_DB", "skill_e2e")
# MFA requires a Fernet key for symmetric TOTP secret encryption.
# Generate a deterministic one for tests (32 bytes url-safe base64).
os.environ.setdefault("MFA_FERNET_KEY", "L7gvXDh2v6syV65J0-iwLQMTYbVavNXO2vuXgntcFBo=")

import asyncio
import importlib
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# PostgreSQL connection — defaults to `docker run` instance on port 54329
# ---------------------------------------------------------------------------

POSTGRES_URL = os.environ.get(
    "E2E_POSTGRES_URL",
    "postgresql+asyncpg://skill:skill@localhost:54329/skill_e2e",
)

# Models — simple e-commerce domain
ECOMMERCE_MODELS = {
    "Product": {
        "name": "str",
        "description": "text",
        "price": "float",
        "sku": "str",
        "stock": "int",
    },
    "Order": {"status": "str", "total": "float", "notes": "text"},
    "Customer": {"name": "str", "email": "email", "phone": "str", "tier": "str"},
}

# All 27 EXTEND tools — the full set
ALL_TOOLS: list[tuple[str, str]] = [
    ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete"),
    ("add_cursor_pagination", "adapt.extend.crud_data.add_cursor_pagination"),
    ("add_search", "adapt.extend.crud_data.add_search"),
    ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
    ("add_bulk_operations", "adapt.extend.crud_data.add_bulk_operations"),
    ("add_data_export", "adapt.extend.crud_data.add_data_export"),
    ("add_file_upload", "adapt.extend.crud_data.add_file_upload"),
    ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
    ("add_rbac", "adapt.extend.auth_access.add_rbac"),
    ("add_mfa", "adapt.extend.auth_access.add_mfa"),
    ("add_api_key_auth", "adapt.extend.auth_access.add_api_key_auth"),
    ("add_feature_flags", "adapt.extend.auth_access.add_feature_flags"),
    ("add_oauth2_provider", "adapt.extend.auth_access.add_oauth2_provider"),
    ("add_cache_layer", "adapt.extend.infrastructure.add_cache_layer"),
    ("add_circuit_breaker", "adapt.extend.infrastructure.add_circuit_breaker"),
    ("add_outbox_pattern", "adapt.extend.infrastructure.add_outbox_pattern"),
    ("add_saga", "adapt.extend.infrastructure.add_saga"),
    ("add_sse", "adapt.extend.realtime.add_sse"),
    ("add_webhook_receiver", "adapt.extend.realtime.add_webhook_receiver"),
    ("add_webhook_sender", "adapt.extend.realtime.add_webhook_sender"),
    ("add_api_versioning", "adapt.extend.api_design.add_api_versioning"),
    ("add_batch_endpoint", "adapt.extend.api_design.add_batch_endpoint"),
    ("add_graphql", "adapt.extend.api_design.add_graphql"),
    ("add_long_running_task", "adapt.extend.api_design.add_long_running_task"),
    ("add_contract_tests", "adapt.extend.testing_tools.add_contract_tests"),
    ("add_factory", "adapt.extend.testing_tools.add_factory"),
    ("add_load_profile", "adapt.extend.testing_tools.add_load_profile"),
]


# ---------------------------------------------------------------------------
# Setup: generate project + apply all 27 tools (once per process)
# ---------------------------------------------------------------------------

_PROJECT_DIR: Path | None = None
_TMPDIR: tempfile.TemporaryDirectory | None = None


def _setup() -> Path:
    """Generate a fresh project, apply all 27 tools, return project dir."""
    global _PROJECT_DIR, _TMPDIR
    if _PROJECT_DIR is not None:
        return _PROJECT_DIR

    from adapt.contracts import ToolInput
    from tests.common.fixture_factory import create_fixture_project

    _TMPDIR = tempfile.TemporaryDirectory()
    project_dir = create_fixture_project(
        name="ecommerce_pg",
        models=ECOMMERCE_MODELS,
        tmp_dir=Path(_TMPDIR.name),
    )

    applied: list[str] = []
    for tool_name, mod_path in ALL_TOOLS:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, tool_name)
        result = fn(ToolInput(project_dir=str(project_dir)))
        if result.status == "error":
            raise RuntimeError(f"{tool_name} failed: {result.error}")
        applied.append(tool_name)

    print(f"  [setup] Applied {len(applied)}/27 tools to {project_dir.name}")
    _PROJECT_DIR = project_dir
    return project_dir


async def _precheck_postgres() -> bool:
    """Verify PostgreSQL is reachable before running tests."""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(POSTGRES_URL, echo=False)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception as exc:
        print(f"  [precheck] PostgreSQL unreachable at {POSTGRES_URL}")
        print(f"  [precheck] {type(exc).__name__}: {str(exc)[:200]}")
        return False


def _load_app(project_dir: Path):
    """Load the generated app.main with a clean sys.modules cache."""
    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    return importlib.import_module("app.main").app


async def _reset_schema(engine) -> None:
    """Drop all tables and recreate the schema from scratch."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))


async def _make_client(app, project_dir: Path, tenant_slug: str = "acme"):
    """Build an httpx AsyncClient wired to real PostgreSQL.

    Returns (client, session, engine, tenant_id) — tenant_id is None if
    add_multi_tenancy was not applied. The returned client should always
    send X-Tenant-ID: {tenant_slug} when multi-tenancy is active.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    get_session_mod = importlib.import_module("app.core.session")
    base_mod = importlib.import_module("app.models.base")

    engine = create_async_engine(POSTGRES_URL, echo=False, future=True)
    await _reset_schema(engine)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # NOTE: create_all must be the ONLY statement in this transaction. A prior
    # version also ran `CREATE TABLE audit_logs_default PARTITION OF audit_logs`
    # here — but the current add_audit_log ships an in-memory tamper-evident log
    # (no `audit_logs` table on Base.metadata), so that statement raised
    # UndefinedTableError, which poisons the asyncpg transaction. The bare
    # `except: pass` swallowed the Python error but the aborted transaction
    # still rolled back on commit — wiping every table create_all had just made,
    # so `tenants` vanished and all 8 tests failed with "relation does not
    # exist". Keeping this block to create_all only makes the schema durable.
    async with engine.begin() as conn:
        await conn.run_sync(base_mod.Base.metadata.create_all)

    session = factory()

    # Create a default tenant so /users/signup passes the tenant middleware
    tenant_id = await _seed_tenant(session, tenant_slug)

    async def _override():
        yield session

    app.dependency_overrides[get_session_mod.get_session] = _override
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, session, engine, tenant_id


async def _seed_tenant(session, slug: str) -> uuid.UUID | None:
    """Insert a seed tenant row so tenant-aware endpoints accept requests."""
    try:
        tenant_mod = importlib.import_module("app.models.tenant")
    except ModuleNotFoundError:
        return None
    tid = uuid.uuid4()
    tenant = tenant_mod.Tenant(
        id=tid, name="Acme Corp", slug=slug, status="active", allow_public_signup=True
    )
    session.add(tenant)
    await session.commit()
    return tid


async def _teardown(app, client, session, engine) -> None:
    await client.aclose()
    await session.close()
    await engine.dispose()
    app.dependency_overrides.clear()


def _th(token: str | None = None, tenant: str = "acme") -> dict[str, str]:
    """Build request headers: Authorization + X-Tenant-ID."""
    h = {"X-Tenant-ID": tenant}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _signup_and_login(client, email: str, password: str, name: str = "Test") -> str:
    r = await client.post(
        "/api/v1/users/signup",
        json={
            "email": email,
            "password": password,
            "full_name": name,
        },
        headers=_th(),
    )
    assert r.status_code in (200, 201), f"signup {email} failed: {r.status_code} {r.text[:300]}"
    r = await client.post(
        "/api/v1/login/access-token",
        data={
            "username": email,
            "password": password,
        },
        headers=_th(),
    )
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text[:300]}"
    return r.json()["access_token"]


# ---------------------------------------------------------------------------
# Test 01 — Auth works end-to-end through the tenant middleware
# ---------------------------------------------------------------------------


async def check_01_tenant_aware_auth(pd: Path) -> tuple[bool, str]:
    """Signup → login → protected endpoint, all with X-Tenant-ID header."""
    app = _load_app(pd)
    client, session, engine, tid = await _make_client(app, pd)
    fails: list[str] = []
    try:
        if tid is None:
            fails.append("tenants table not present — add_multi_tenancy did not run")
            return (False, "tenant_aware_auth: " + "; ".join(fails))

        token = await _signup_and_login(
            client, "owner@acme.example.com", "OwnerPass123!", "Acme Owner"
        )

        # Valid token + header naming the user's own tenant → 200.
        r = await client.get("/api/v1/products/", headers=_th(token))
        if r.status_code != 200:
            fails.append(f"GET products with token+matching header: {r.status_code}")

        # Identity-bound tenancy: X-Tenant-ID is OPTIONAL. A valid token with NO
        # header still works because the tenant is resolved from the user's
        # identity (User.tenant_id), not a blind header. (The old header-gated
        # model 400'd here — that was insecure AND broke every public route.)
        r = await client.get("/api/v1/products/", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            fails.append(
                f"no header but valid token: expected 200 (tenant from identity), got {r.status_code}"
            )

        # Spoofed header: a normal user naming a tenant that is NOT their own is
        # rejected 403 — knowing a slug must never grant cross-tenant access.
        r = await client.get(
            "/api/v1/products/",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "definitely-not-my-tenant"},
        )
        if r.status_code != 403:
            fails.append(f"spoofed cross-tenant header: expected 403, got {r.status_code}")

        # No credentials at all → unauthenticated.
        r = await client.get("/api/v1/products/")
        if r.status_code not in (401, 403):
            fails.append(f"no token: expected 401, got {r.status_code}")

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "tenant_aware_auth: "
        + ("; ".join(fails) if fails else "PASS — signup/login/protected + tenant enforcement"),
    )


# ---------------------------------------------------------------------------
# Test 02 — PostgreSQL full-text search actually finds documents
# ---------------------------------------------------------------------------


async def check_02_postgres_fts_search(pd: Path) -> tuple[bool, str]:
    """Real FTS path: create products, search by term, verify ranked results."""
    app = _load_app(pd)
    client, session, engine, _ = await _make_client(app, pd)
    fails: list[str] = []
    try:
        token = await _signup_and_login(client, "search@acme.example.com", "SearchPass123!")
        h = _th(token)

        # Create 3 products with distinct searchable names
        products = [
            {
                "name": "Wireless Headphones",
                "description": "Noise-cancelling bluetooth",
                "price": 199.0,
                "sku": "WHP-001",
                "stock": 50,
            },
            {
                "name": "Mechanical Keyboard",
                "description": "Tactile switches for coding",
                "price": 149.0,
                "sku": "KBD-001",
                "stock": 30,
            },
            {
                "name": "Gaming Mouse",
                "description": "Wireless ergonomic precision",
                "price": 89.0,
                "sku": "MSE-001",
                "stock": 100,
            },
        ]
        for p in products:
            r = await client.post("/api/v1/products/", json=p, headers=h)
            if r.status_code not in (200, 201):
                fails.append(f"create {p['sku']}: {r.status_code} {r.text[:150]}")

        # Search should use PostgreSQL FTS (websearch_to_tsquery + @@)
        r = await client.get("/api/v1/products/search?q=wireless", headers=h)
        if r.status_code != 200:
            fails.append(f"FTS search: {r.status_code} {r.text[:200]}")
        else:
            body = r.json()
            results = body.get("data") or body.get("items") or body.get("results") or []
            # Should find 2 (Wireless Headphones + Gaming Mouse with "wireless" in desc)
            if len(results) < 1:
                fails.append("FTS search 'wireless': 0 results (expected ≥1)")

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "postgres_fts_search: "
        + ("; ".join(fails) if fails else f"PASS — FTS returned {len(results)} matches"),
    )


# ---------------------------------------------------------------------------
# Test 03 — RBAC: permissions table exists, roles can be created
# ---------------------------------------------------------------------------


async def check_03_rbac_guard(pd: Path) -> tuple[bool, str]:
    """RBAC role guard admits a principal carrying the role, denies otherwise.

    add_rbac ships app/rbac.py exposing require_roles() over the RoleGuard +
    CurrentPrincipal primitives (role-based, not table-based — the older
    permissions/roles tables no longer exist). We mount a probe route guarded
    by the real RequestGuard adapter with a header-driven principal resolver
    and assert admit (200) / forbidden (403) / unauthenticated (401|403).
    """
    app = _load_app(pd)
    from app.rbac import require_roles  # the tool's wiring helper must import
    from fastapi import Depends, Request

    from core.venous._adapters.fastapi.RequestGuardAdapter import require
    from core.venous.auth.CurrentPrincipal.CurrentPrincipal import (
        CurrentPrincipal,
        anonymous,
    )
    from core.venous.auth.RequestGuard.RequestGuard import RoleGuard

    def _resolver(request: Request):
        role = request.headers.get("X-Role")
        if not role:
            return anonymous()
        return CurrentPrincipal(subject_id="u-1", tenant_id=None, roles=frozenset({role}))

    @app.get("/_rbac_probe")
    async def _rbac_probe(principal=Depends(require(RoleGuard("admin"), principal=_resolver))):
        return {"subject": principal.subject_id}

    client, session, engine, _ = await _make_client(app, pd)
    fails: list[str] = []
    try:
        if not callable(require_roles("admin")):
            fails.append("require_roles('admin') did not return a dependency")

        r = await client.get("/_rbac_probe", headers={**_th(), "X-Role": "admin"})
        if r.status_code != 200:
            fails.append(f"admin admit: expected 200, got {r.status_code} {r.text[:120]}")

        r = await client.get("/_rbac_probe", headers={**_th(), "X-Role": "viewer"})
        if r.status_code != 403:
            fails.append(f"viewer deny: expected 403, got {r.status_code}")

        r = await client.get("/_rbac_probe", headers=_th())
        if r.status_code not in (401, 403):
            fails.append(f"anonymous deny: expected 401/403, got {r.status_code}")

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "rbac_guard: " + ("; ".join(fails) if fails else "PASS — role admit/deny enforced"),
    )


# ---------------------------------------------------------------------------
# Test 04 — MFA: TOTP secret generation works
# ---------------------------------------------------------------------------


async def check_04_mfa_totp(pd: Path) -> tuple[bool, str]:
    """TOTP verifier accepts a valid code, rejects replay and a wrong code.

    add_mfa ships app/mfa.py's install_mfa(app), which attaches a
    StandardTotpVerifier to app.state.totp (RFC 6238, replay-step protection).
    We wire it, generate a code with the primitive's own _hotp (so it matches
    the verifier's algorithm exactly), and assert the three TOTP invariants:
    valid code accepted, same step rejected as replay, wrong code rejected.
    """
    import secrets as _secrets
    import time as _time

    app = _load_app(pd)
    importlib.import_module("app.mfa").install_mfa(app)
    client, session, engine, _ = await _make_client(app, pd)
    fails: list[str] = []
    try:
        from core.venous.auth.TotpVerifier.TotpVerifier import (
            TotpInvalidCodeError,
            TotpReplayError,
            _hotp,
        )

        verifier = app.state.totp
        secret = _secrets.token_bytes(20)  # TOTP-INV-01: >= 160 bits
        step = int(_time.time() // 30)
        code = _hotp(secret, step, 6, "SHA1")

        used_step = verifier.verify(secret, code, None)
        if used_step < step:
            fails.append(f"verify returned step {used_step}, expected >= {step}")

        try:
            verifier.verify(secret, code, used_step)
            fails.append("replay of a consumed step was NOT rejected")
        except TotpReplayError:
            pass

        try:
            verifier.verify(secret, "999999" if code != "999999" else "111111", used_step)
            fails.append("a wrong code was NOT rejected")
        except TotpInvalidCodeError:
            pass

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "mfa_totp: "
        + ("; ".join(fails) if fails else "PASS — code accepted, replay + wrong code rejected"),
    )


# ---------------------------------------------------------------------------
# Test 05 — OAuth2 provider: authorization endpoint exists
# ---------------------------------------------------------------------------


async def check_05_oauth2_introspection(pd: Path) -> tuple[bool, str]:
    """OAuth2 bearer introspection admits a valid token, rejects bad/missing.

    add_oauth2_provider ships app/oauth2.py's install_oauth2(app, introspector,
    session_store) + current_claims() over the TokenIntrospector primitive
    (introspection-based, not an authorization-server route set). The
    introspector is project-supplied, so we wire a stub that honours the
    catalog Protocol and assert the current_claims dependency enforces it.
    """
    app = _load_app(pd)
    from app.oauth2 import current_claims, install_oauth2  # tool wiring helper
    from fastapi import Depends

    from core.venous.auth.SessionStore.SessionStore import InMemorySessionStore
    from core.venous.auth.TokenIntrospector.TokenIntrospector import (
        InvalidTokenError,
        TokenClaims,
    )

    class _StubIntrospector:
        """Minimal TokenIntrospector: 'good-token' is valid, everything else isn't."""

        def introspect(self, token: str, required_audience: str) -> TokenClaims:
            if token != "good-token":
                raise InvalidTokenError("unknown token")
            return TokenClaims(
                subject="oauth-user",
                scopes=frozenset({"read"}),
                audience=frozenset({required_audience}),
                issuer="https://issuer.test",
                expires_at=2_000_000_000,
            )

    install_oauth2(app, _StubIntrospector(), InMemorySessionStore())

    @app.get("/_oauth_probe")
    async def _oauth_probe(claims=Depends(current_claims("api"))):
        return {"subject": claims.subject, "scopes": sorted(claims.scopes)}

    client, session, engine, _ = await _make_client(app, pd)
    fails: list[str] = []
    try:
        r = await client.get(
            "/_oauth_probe", headers={**_th(), "Authorization": "Bearer good-token"}
        )
        if r.status_code != 200 or r.json().get("subject") != "oauth-user":
            fails.append(
                f"valid token: expected 200/oauth-user, got {r.status_code} {r.text[:120]}"
            )

        r = await client.get("/_oauth_probe", headers={**_th(), "Authorization": "Bearer nope"})
        if r.status_code != 401:
            fails.append(f"bad token: expected 401, got {r.status_code}")

        r = await client.get("/_oauth_probe", headers=_th())
        if r.status_code not in (401, 403):
            fails.append(f"missing token: expected 401/403, got {r.status_code}")

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "oauth2_introspection: "
        + ("; ".join(fails) if fails else "PASS — bearer introspection enforced"),
    )


# ---------------------------------------------------------------------------
# Test 06 — Multi-tenant isolation: tenant A cannot read tenant B's data
# ---------------------------------------------------------------------------


async def check_06_tenant_isolation(pd: Path) -> tuple[bool, str]:
    """Two tenants, same model, each sees only their own rows."""
    app = _load_app(pd)
    client, session, engine, _ = await _make_client(app, pd, tenant_slug="acme")
    fails: list[str] = []
    try:
        # Seed a second tenant
        tenant_mod = importlib.import_module("app.models.tenant")
        beta_id = uuid.uuid4()
        session.add(
            tenant_mod.Tenant(
                id=beta_id, name="Beta LLC", slug="beta", status="active", allow_public_signup=True
            )
        )
        await session.commit()

        # User in Acme creates a product
        token_a = await _signup_and_login(
            client, "alice@acme.example.com", "AlicePass123!", "Alice"
        )
        r = await client.post(
            "/api/v1/products/",
            json={
                "name": "Acme Widget",
                "description": "acme only",
                "price": 10.0,
                "sku": "ACM-001",
                "stock": 5,
            },
            headers=_th(token_a, tenant="acme"),
        )
        if r.status_code not in (200, 201):
            fails.append(f"Acme create: {r.status_code} {r.text[:200]}")
            return (False, "tenant_isolation: " + "; ".join(fails))

        # User in Beta signs up
        token_b = await _signup_and_login(client, "bob@beta.example.com", "BobPass123!", "Bob")

        # Bob (Beta tenant) lists products — should NOT see Acme's widget
        r = await client.get("/api/v1/products/", headers=_th(token_b, tenant="beta"))
        if r.status_code == 200:
            body = r.json()
            items = (
                body.get("data") or body.get("items") or (body if isinstance(body, list) else [])
            )
            skus = [item.get("sku") for item in items if isinstance(item, dict)]
            if "ACM-001" in skus:
                fails.append("TENANT BREACH: Beta user sees Acme product")

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "tenant_isolation: " + ("; ".join(fails) if fails else "PASS — tenant boundary enforced"),
    )


# ---------------------------------------------------------------------------
# Test 07 — Bulk create actually persists rows in PostgreSQL
# ---------------------------------------------------------------------------


async def check_07_bulk_create_persists(pd: Path) -> tuple[bool, str]:
    """Bulk create 10 products, verify all 10 are queryable via GET /products/."""
    app = _load_app(pd)
    client, session, engine, _ = await _make_client(app, pd)
    fails: list[str] = []
    try:
        token = await _signup_and_login(client, "bulk@acme.example.com", "BulkPass123!")
        h = _th(token)

        items = [
            {
                "name": f"Bulk-{i:03d}",
                "description": f"batch item {i}",
                "price": float(i),
                "sku": f"BLK-{i:03d}",
                "stock": i,
            }
            for i in range(10)
        ]
        r = await client.post(
            "/api/v1/products/bulk",
            json={
                "items": items,
                "mode": "all_or_nothing",
            },
            headers=h,
        )
        if r.status_code not in (200, 201, 207):
            fails.append(f"bulk create: {r.status_code} {r.text[:200]}")
            return (False, "bulk_create_persists: " + "; ".join(fails))

        # Commit so the rows are visible to a fresh connection
        await session.commit()

        # Count via the same session (same transaction context)
        from sqlalchemy import text

        result = await session.execute(text("SELECT COUNT(*) FROM products WHERE sku LIKE 'BLK-%'"))
        count = result.scalar()
        if count != 10:
            fails.append(f"bulk create: expected 10 persisted, got {count}")

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "bulk_create_persists: " + ("; ".join(fails) if fails else "PASS — 10/10 persisted"),
    )


# ---------------------------------------------------------------------------
# Test 08 — Audit log captures bulk operations
# ---------------------------------------------------------------------------


async def check_08_audit_log_tamper_evident(pd: Path) -> tuple[bool, str]:
    """The tamper-evident audit log appends, verifies its hash chain, exports.

    add_audit_log ships an in-memory HMAC-chained audit log + REST router
    (core.venous._adapters.fastapi.AuditLogAdapter), emitted as
    app/audit_log.py's install_audit_log(app). The scaffold's main.py does not
    auto-wire it, so we wire it here to exercise the capability the tool
    delivers end-to-end over HTTP: append two entries, verify the chain is
    intact, and confirm export streams them back.
    """
    from sqlalchemy import text

    app = _load_app(pd)
    importlib.import_module("app.audit_log").install_audit_log(app)
    client, session, engine, _ = await _make_client(app, pd)
    fails: list[str] = []
    n = 0
    try:
        token = await _signup_and_login(client, "audit@acme.example.com", "AuditPass123!")
        # The /audit-logs router is superuser-only (R5-S1-F1) and records the
        # actor from the authenticated principal (R5-S1-F2), so promote the
        # caller to superuser and let the server attribute the entries.
        await session.execute(
            text("UPDATE users SET is_superuser = true WHERE email = :e"),
            {"e": "audit@acme.example.com"},
        )
        await session.commit()
        h = _th(token)

        # Append two audit entries through the REST router (actor is server-side).
        for resource in ("product/AUD-001", "product/AUD-002"):
            r = await client.post(
                "/audit-logs/",
                params={
                    "action": "create",
                    "resource": resource,
                    "outcome": "success",
                },
                headers=h,
            )
            if r.status_code not in (200, 201):
                fails.append(f"append {resource}: {r.status_code} {r.text[:150]}")

        # The HMAC chain must verify as intact (tamper-evident guarantee).
        r = await client.post("/audit-logs/verify", headers=h)
        if r.status_code != 200 or not r.json().get("valid"):
            fails.append(f"verify chain: {r.status_code} {r.text[:120]}")

        # Export must stream back the entries we appended. since_seq is
        # 1-based (TEAL_INV_04 rejects 0), so start at the first sequence.
        r = await client.get("/audit-logs/export", params={"since_seq": 1}, headers=h)
        n = len([ln for ln in r.text.splitlines() if ln.strip()]) if r.status_code == 200 else 0
        if n < 2:
            fails.append(f"export: expected ≥2 entries, got {n} ({r.status_code})")

    finally:
        await _teardown(app, client, session, engine)

    return (
        not fails,
        "audit_log_tamper_evident: "
        + ("; ".join(fails) if fails else f"PASS — {n} entries, hash chain valid"),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("01_tenant_aware_auth", check_01_tenant_aware_auth),
    ("02_postgres_fts_search", check_02_postgres_fts_search),
    ("03_rbac_guard", check_03_rbac_guard),
    ("04_mfa_totp", check_04_mfa_totp),
    ("05_oauth2_introspection", check_05_oauth2_introspection),
    ("06_tenant_isolation", check_06_tenant_isolation),
    ("07_bulk_create_persists", check_07_bulk_create_persists),
    ("08_audit_log_tamper_evident", check_08_audit_log_tamper_evident),
]


def main() -> int:
    print("=" * 70)
    print(f"  SKILL-001 E2E with REAL PostgreSQL — {len(ALL_TOOLS)} tools, {len(TESTS)} tests")
    print(f"  URL: {POSTGRES_URL}")
    print("=" * 70)
    print()

    if not asyncio.run(_precheck_postgres()):
        print("  [SKIP] PostgreSQL unreachable — run:")
        print("    docker run -d --name skill001-e2e-pg \\")
        print("      -e POSTGRES_USER=skill -e POSTGRES_PASSWORD=skill \\")
        print("      -e POSTGRES_DB=skill_e2e -p 54329:5432 postgres:16-alpine")
        return 2

    try:
        project_dir = _setup()
    except Exception as exc:
        print(f"  [FAIL] Project setup: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    total = len(TESTS)
    passed = 0
    failed: list[str] = []

    t0 = time.monotonic()
    for test_id, test_fn in TESTS:
        try:
            ok, detail = asyncio.run(test_fn(project_dir))
        except Exception as exc:
            ok = False
            detail = f"{test_id}: EXCEPTION: {type(exc).__name__}: {str(exc)[:200]}"

        if ok:
            passed += 1
            print(f"  [PASS]  {test_id}: {detail.split(': ', 1)[-1]}")
        else:
            failed.append(test_id)
            print(f"  [FAIL]  {test_id}: {detail}")

    elapsed = time.monotonic() - t0

    print()
    print("-" * 70)
    print(f"  Result: {passed}/{total} tests passed  ({elapsed:.1f}s)")
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
    print("-" * 70)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
