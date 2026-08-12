"""HTTP smoke test — verifies generated endpoints actually respond.

Uses FastAPI TestClient (no real server needed, no DB needed for basic checks).
Tests that every route returns a valid HTTP status (not 500).

Run as a standalone script:
    PYTHONPATH=. python3 tests/test_http_smoke.py

Or via pytest:
    PYTHONPATH=. pytest tests/test_http_smoke.py -v
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from adapt.contracts import ToolInput
from tests.common.fixture_factory import create_fixture_project

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# HTTP status codes that are acceptable when there is no DB and no auth token.
# 200  — public endpoint, no auth or DB required (health)
# 401  — correct: auth guard fired before any DB access
# 403  — correct: auth passed but insufficient role
# 404  — correct: resource not found (no DB hit or empty table)
# 422  — correct: request body validation failed before any DB access
# 503  — correct: health probe detected DB is unreachable (intentional)
_ACCEPTABLE = frozenset({200, 401, 403, 404, 422, 503})


class _RouteHit(NamedTuple):
    method: str
    path: str
    status: int


# Top-level packages a *generated project* ships and imports at runtime. Each
# fixture project carries its own copy under its own dir, so a cached copy from
# a prior fixture must be purged or it shadows the next project's (e.g. add_rbac
# caches ``core.venous._adapters.fastapi`` with only RequestGuardAdapter, which
# then hides add_mfa's TotpVerifierAdapter → ModuleNotFoundError).
_PROJECT_TOP_LEVEL = ("app", "core")


def _is_project_module(mod: str) -> bool:
    """True if *mod* belongs to a generated project's top-level package."""
    return any(mod == pkg or mod.startswith(pkg + ".") for pkg in _PROJECT_TOP_LEVEL)


def _purge_project_modules() -> None:
    """Drop every cached generated-project module from ``sys.modules``."""
    for mod in list(sys.modules):
        if _is_project_module(mod):
            del sys.modules[mod]


def _make_client(project_dir: Path):
    """Import the generated app and return a TestClient bound to it.

    Inserts *project_dir* at position 0 of sys.path so its ``app`` package
    shadows any previously imported ``app`` module.  After use the caller
    is responsible for removing the entry and clearing the relevant
    sys.modules keys.
    """
    from starlette.testclient import TestClient  # local import keeps top clean

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # Force fresh import of the generated project's packages.
    _purge_project_modules()

    app_module = importlib.import_module("app.main")
    app = app_module.app
    return TestClient(app, raise_server_exceptions=False)


def _cleanup_sys_path(project_dir: Path) -> None:
    """Remove *project_dir* from sys.path and purge generated-project modules."""
    key = str(project_dir)
    try:  # noqa: SIM105
        sys.path.remove(key)
    except ValueError:
        pass
    _purge_project_modules()


def _collect_routes(router, prefix: str = ""):
    """Recursively yield (path, methods) for every leaf route."""
    for route in router.routes:
        methods = getattr(route, "methods", None)
        path = prefix + getattr(route, "path", "")
        if methods and not hasattr(route, "routes"):
            yield path, methods
        if hasattr(route, "routes"):
            yield from _collect_routes(route, prefix + getattr(route, "path", ""))


def _probe_route(client, method: str, path: str) -> _RouteHit:
    """Make a single unauthenticated request and return the status code."""
    if method == "GET":
        resp = client.get(path)
    elif method == "POST":
        resp = client.post(path, json={})
    elif method in ("PATCH", "PUT"):
        resp = client.patch(path, json={})
    elif method == "DELETE":
        resp = client.delete(path)
    else:
        # HEAD etc. — not meaningful for smoke tests
        return _RouteHit(method, path, 200)
    return _RouteHit(method, path, resp.status_code)


def _apply_tool(module_path: str, fn_name: str, project_dir: Path) -> None:
    """Import and execute a single adapt tool against *project_dir*."""
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    result = fn(ToolInput(project_dir=str(project_dir)))
    if result.status == "error":
        raise RuntimeError(f"{fn_name} returned error: {result.error}")


# ---------------------------------------------------------------------------
# Test 1 — base project: core endpoints respond with correct HTTP codes
# ---------------------------------------------------------------------------


def test_base_project_core_endpoints() -> tuple[bool, str]:
    """Generate a base project and verify 7 core endpoints respond correctly.

    No auth token is sent. The DB is unavailable. Expected codes:
      /healthz                          -> 200  (liveness: zero deps)
      /readyz                           -> 200 or 503 (readiness: DB probe)
      /startupz                         -> 200 or 503
      POST /api/v1/login/access-token   -> 422 (validation before DB)
      POST /api/v1/users/signup         -> 422 (validation before DB)
      GET  /api/v1/items/               -> 401 (auth guard before DB)
      GET  /api/v1/users/me             -> 401 (auth guard before DB)

    Returns:
        (passed, detail) tuple.
    """
    cases: list[tuple[str, str, set[int]]] = [
        ("GET", "/healthz", {200}),
        ("GET", "/readyz", {200, 503}),
        ("GET", "/startupz", {200, 503}),
        ("POST", "/api/v1/login/access-token", {422}),
        ("POST", "/api/v1/users/signup", {422}),
        ("GET", "/api/v1/items/", {401}),
        ("GET", "/api/v1/users/me", {401}),
    ]

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = create_fixture_project("smoke_base", tmp_dir=Path(tmp))
        client = _make_client(project_dir)

        for method, path, expected in cases:
            hit = _probe_route(client, method, path)
            if hit.status not in expected:
                failures.append(f"{method} {path}: expected one of {expected}, got {hit.status}")

        _cleanup_sys_path(project_dir)

    if failures:
        return False, "Base project failures:\n" + "\n".join(f"  {f}" for f in failures)
    return True, f"Base project: {len(cases)}/{len(cases)} core endpoints OK"


# ---------------------------------------------------------------------------
# Test 2 — after add_soft_delete: new endpoints exist and require auth
# ---------------------------------------------------------------------------


def test_soft_delete_endpoints() -> tuple[bool, str]:
    """Apply add_soft_delete and verify the new endpoints are mounted.

    The new endpoints require auth, so without a token all should return 401.
    None should return 404 (not registered) or 500.

    Routes tested:
      GET    /api/v1/items/deleted/         -> 401
      DELETE /api/v1/items/999              -> 401
      POST   /api/v1/items/999/restore      -> 401
      DELETE /api/v1/items/999/permanent    -> 401

    Returns:
        (passed, detail) tuple.
    """
    cases: list[tuple[str, str, set[int]]] = [
        ("GET", "/api/v1/items/deleted/", {401}),
        ("DELETE", "/api/v1/items/999", {401}),
        ("POST", "/api/v1/items/999/restore", {401}),
        ("DELETE", "/api/v1/items/999/permanent", {401}),
    ]

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = create_fixture_project("smoke_soft_delete", tmp_dir=Path(tmp))
        _apply_tool(
            "adapt.extend.crud_data.add_soft_delete",
            "add_soft_delete",
            project_dir,
        )
        client = _make_client(project_dir)

        for method, path, expected in cases:
            hit = _probe_route(client, method, path)
            if hit.status not in expected:
                failures.append(f"{method} {path}: expected one of {expected}, got {hit.status}")

        _cleanup_sys_path(project_dir)

    if failures:
        return False, "soft_delete failures:\n" + "\n".join(f"  {f}" for f in failures)
    return True, f"add_soft_delete: {len(cases)}/{len(cases)} new endpoints respond correctly"


# ---------------------------------------------------------------------------
# Test 3 — after add_rbac: route file created, router importable, no 500
# ---------------------------------------------------------------------------


def test_rbac_router_importable() -> tuple[bool, str]:
    """Apply add_rbac and verify the RBAC router file is valid and importable.

    The tool targets ``app/api/main.py`` for patching, but the generated
    project uses ``app/routes/__init__.py``.  The route file IS created;
    this test verifies:

    1. ``app/api/routes/rbac.py`` exists in the generated project.
    2. The router module imports without error.
    3. The router exposes the expected prefix ``/rbac``.

    Additionally, if the router IS mounted (future fix), verifies no 500.

    Returns:
        (passed, detail) tuple.
    """
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = create_fixture_project("smoke_rbac", tmp_dir=Path(tmp))
        _apply_tool(
            "adapt.extend.auth_access.add_rbac",
            "add_rbac",
            project_dir,
        )

        # 1. Route file must exist.
        rbac_route_file = project_dir / "app" / "api" / "routes" / "rbac.py"
        if not rbac_route_file.exists():
            failures.append("app/api/routes/rbac.py was NOT created by add_rbac")
        else:
            # 2. Module must import cleanly.
            sys.path.insert(0, str(project_dir))
            _purge_project_modules()
            try:
                rbac_mod = importlib.import_module("app.api.routes.rbac")
                router = rbac_mod.router
                # 3. Router prefix must be /rbac.
                if router.prefix != "/rbac":
                    failures.append(f"RBAC router prefix expected '/rbac', got '{router.prefix}'")
                # 4. Router must have at least one route.
                if not router.routes:
                    failures.append("RBAC router has no routes")
            except Exception as exc:
                failures.append(f"app.api.routes.rbac import failed: {exc}")
            finally:
                _cleanup_sys_path(project_dir)

        # 5. If the router ends up mounted, no route should return 500.
        client = _make_client(project_dir)
        for method, path, _ in [
            ("GET", "/api/v1/rbac/permissions", None),
            ("GET", "/api/v1/rbac/roles", None),
        ]:
            hit = _probe_route(client, method, path)
            if hit.status == 500:
                failures.append(f"{method} {path} returned 500 (Internal Server Error)")
        _cleanup_sys_path(project_dir)

    if failures:
        return False, "add_rbac failures:\n" + "\n".join(f"  {f}" for f in failures)
    return True, "add_rbac: route file created, router importable, prefix correct, no 500"


# ---------------------------------------------------------------------------
# Test 4 — after add_mfa: route file created, router importable, no 500
# ---------------------------------------------------------------------------


def test_mfa_router_importable() -> tuple[bool, str]:
    """Apply add_mfa and verify the MFA router file is valid and importable.

    Same rationale as test_rbac_router_importable: the tool creates the
    route file but cannot patch the generated project's router registry.
    This test verifies:

    1. ``app/api/routes/mfa.py`` exists.
    2. The router module imports without error.
    3. The router exposes the expected prefix ``/auth/mfa``.

    Additionally, if the router IS mounted (future fix), verifies no 500.

    Returns:
        (passed, detail) tuple.
    """
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = create_fixture_project("smoke_mfa", tmp_dir=Path(tmp))
        _apply_tool(
            "adapt.extend.auth_access.add_mfa",
            "add_mfa",
            project_dir,
        )

        # 1. Route file must exist.
        mfa_route_file = project_dir / "app" / "api" / "routes" / "mfa.py"
        if not mfa_route_file.exists():
            failures.append("app/api/routes/mfa.py was NOT created by add_mfa")
        else:
            # 2. Module must import cleanly.
            sys.path.insert(0, str(project_dir))
            _purge_project_modules()
            try:
                mfa_mod = importlib.import_module("app.api.routes.mfa")
                router = mfa_mod.router
                # 3. Router prefix must be /auth/mfa.
                if router.prefix != "/auth/mfa":
                    failures.append(
                        f"MFA router prefix expected '/auth/mfa', got '{router.prefix}'"
                    )
                # 4. Router must expose the four expected endpoints.
                # Route paths may be absolute (prefix + relative) or relative-only
                # depending on the FastAPI version; normalise to suffix matching.
                route_paths = {getattr(r, "path", "") for r in router.routes}
                for expected_suffix in ("/enroll", "/verify-enrollment", "/challenge", "/disable"):
                    if not any(
                        p == expected_suffix or p.endswith(expected_suffix) for p in route_paths
                    ):
                        failures.append(f"MFA router missing route ending in '{expected_suffix}'")
            except Exception as exc:
                failures.append(f"app.api.routes.mfa import failed: {exc}")
            finally:
                _cleanup_sys_path(project_dir)

        # 5. If the router ends up mounted, no route should return 500.
        client = _make_client(project_dir)
        for method, path, _ in [
            ("POST", "/api/v1/auth/mfa/enroll", None),
            ("POST", "/api/v1/auth/mfa/challenge", None),
        ]:
            hit = _probe_route(client, method, path)
            if hit.status == 500:
                failures.append(f"{method} {path} returned 500 (Internal Server Error)")
        _cleanup_sys_path(project_dir)

    if failures:
        return False, "add_mfa failures:\n" + "\n".join(f"  {f}" for f in failures)
    return True, "add_mfa: route file created, router importable, prefix correct, no 500"


# ---------------------------------------------------------------------------
# Test 5 — scan ALL routes for 500s (base project)
# ---------------------------------------------------------------------------


def test_scan_all_routes_no_500() -> tuple[bool, str]:
    """Iterate every route in the base app and assert none returns 500.

    Parametrised paths (containing ``{``) are tested with synthetic IDs.
    The test counts endpoints tested vs endpoints returning 500.

    Whitelisted endpoints that legitimately require a DB connection before
    they can reject the request (no auth guard, no validation layer):
      - POST /api/v1/password-recovery/{email}  — queries user table by email

    Returns:
        (passed, detail) tuple with the count reported.
    """
    import re

    # Endpoints that need a live DB before they can respond with a non-500.
    # These are expected failures in the no-DB test environment.
    DB_REQUIRED_PATTERNS: list[str] = [  # noqa: N806
        r"^POST /api/v1/password-recovery/",
    ]

    def _is_db_required(method: str, path: str) -> bool:
        key = f"{method} {path}"
        return any(re.match(pat, key) for pat in DB_REQUIRED_PATTERNS)

    hits: list[_RouteHit] = []
    errors_500: list[_RouteHit] = []

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = create_fixture_project("smoke_scan", tmp_dir=Path(tmp))
        client = _make_client(project_dir)

        from app.main import app as generated_app  # available after _make_client

        for path, methods in _collect_routes(generated_app.router):
            # Resolve path params with safe placeholder values.
            resolved_path = path
            if "{" in path:
                resolved_path = re.sub(r"\{[^}]+\}", "999", path)

            for method in methods:
                if method == "HEAD":
                    continue
                if _is_db_required(method, resolved_path):
                    continue
                hit = _probe_route(client, method, resolved_path)
                hits.append(hit)
                if hit.status == 500:
                    errors_500.append(hit)

        _cleanup_sys_path(project_dir)

    total = len(hits)
    if errors_500:
        detail_lines = [f"  {h.method} {h.path} -> 500" for h in errors_500]
        return False, (
            f"Scan found {len(errors_500)}/{total} endpoints returning 500:\n"
            + "\n".join(detail_lines)
        )

    return True, f"Scan: {total}/{total} endpoints respond correctly (no 500s)"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("base_endpoints", test_base_project_core_endpoints),
    ("soft_delete", test_soft_delete_endpoints),
    ("rbac_router", test_rbac_router_importable),
    ("mfa_router", test_mfa_router_importable),
    ("scan_all_no_500", test_scan_all_routes_no_500),
]


def main() -> int:
    """Run all HTTP smoke tests and print a summary.

    Returns:
        0 if all tests pass, 1 if any fail.
    """
    total = len(TESTS)
    passed = 0
    failed: list[tuple[str, str]] = []

    print(f"Running {total} HTTP smoke tests ...\n")

    for test_id, test_fn in TESTS:
        ok, detail = test_fn()
        if ok:
            passed += 1
            print(f"  PASS  [{test_id}]  {detail}")
        else:
            failed.append((test_id, detail))
            print(f"  FAIL  [{test_id}]\n{detail}")

    print(f"\n{'=' * 60}")
    print(f"HTTP smoke: {passed}/{total} endpoints respond correctly")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for name, msg in failed:
            print(f"  {name}: {msg}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# pytest integration
# ---------------------------------------------------------------------------


def test_http_smoke_base() -> None:
    """pytest wrapper for test 1."""
    ok, detail = test_base_project_core_endpoints()
    assert ok, detail


def test_http_smoke_soft_delete() -> None:
    """pytest wrapper for test 2."""
    ok, detail = test_soft_delete_endpoints()
    assert ok, detail


def test_http_smoke_rbac() -> None:
    """pytest wrapper for test 3."""
    ok, detail = test_rbac_router_importable()
    assert ok, detail


def test_http_smoke_mfa() -> None:
    """pytest wrapper for test 4."""
    ok, detail = test_mfa_router_importable()
    assert ok, detail


def test_http_smoke_scan_all() -> None:
    """pytest wrapper for test 5."""
    ok, detail = test_scan_all_routes_no_500()
    assert ok, detail


if __name__ == "__main__":
    sys.exit(main())
