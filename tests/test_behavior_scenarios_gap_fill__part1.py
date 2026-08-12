"""BEHAVIOR scenarios — gap-fill part 1 (scenarios 33-34).

Split from ``test_behavior_scenarios_gap_fill.py`` to respect the 500-LOC
cap. Shared framework lives in
``test_behavior_scenarios_gap_fill__shared.py``.

Covers:
  * SCENARIO 33 — CQRS CommandBus + QueryBus + read-replica routing
  * SCENARIO 34 — CSRF Protection + Input Sanitization
"""

from __future__ import annotations

import ast as _ast

import pytest

from tests.test_behavior_scenarios_gap_fill__shared import (
    Scenario,
    ScenarioContext,
    _assert_scenario,
)

# ===========================================================================
# SCENARIO 33 — CQRS
# ===========================================================================


async def flow_cqrs(ctx: ScenarioContext) -> None:
    """CQRS layer: CommandBus + QueryBus present, read_replica module, routes, config."""
    client = ctx.client
    project_dir = ctx.project_dir

    # --- 1. CommandBus class + dispatch method ---
    cqrs_init = project_dir / "app" / "cqrs" / "__init__.py"
    ctx.record(
        "cqrs_init_exists",
        cqrs_init.exists(),
        str(cqrs_init.relative_to(project_dir) if cqrs_init.exists() else "NOT FOUND"),
    )

    bus_file = project_dir / "app" / "cqrs" / "bus.py"
    if bus_file.exists():
        src = bus_file.read_text()
        ctx.record(
            "command_bus_class_present",
            "class CommandBus" in src,
            "CommandBus class in app/cqrs/bus.py",
        )
        ctx.record(
            "command_bus_dispatch_method",
            "async def dispatch" in src,
            "dispatch method in CommandBus",
        )
        ctx.record(
            "query_bus_class_present",
            "class QueryBus" in src,
            "QueryBus class in app/cqrs/bus.py",
        )
        ctx.record(
            "query_bus_query_method",
            "async def query" in src,
            "query method in QueryBus",
        )
    else:
        for label in [
            "command_bus_class_present",
            "command_bus_dispatch_method",
            "query_bus_class_present",
            "query_bus_query_method",
        ]:
            ctx.record(label, False, "app/cqrs/bus.py not found")

    # --- 2. read_replica module has DATABASE_READ_URL ---
    replica_file = project_dir / "app" / "cqrs" / "read_replica.py"
    if replica_file.exists():
        src = replica_file.read_text()
        ctx.record(
            "read_replica_module_exists",
            True,
            "app/cqrs/read_replica.py present",
        )
        ctx.record(
            "read_replica_database_read_url",
            "DATABASE_READ_URL" in src,
            "DATABASE_READ_URL referenced in read_replica.py",
        )
    else:
        ctx.record("read_replica_module_exists", False, "app/cqrs/read_replica.py not found")
        ctx.record("read_replica_database_read_url", False, "file not found")

    # --- 3. Routes exist: POST /api/v1/cqrs/commands and POST /api/v1/cqrs/queries ---
    # CQRS router is registered in app/routes/__init__.py → mounted under /api/v1.
    # For an unknown command name, the route returns 404 with a structured body explaining
    # "No handler registered for command". We distinguish this from a "route not found" 404
    # by inspecting the response body.
    r_cmd = await client.post(
        "/api/v1/cqrs/commands", json={"name": "UnknownCommand", "payload": {}}
    )
    # Route exists → returns 404 with "No handler" detail OR 405/422 for malformed input.
    # Route missing → returns plain FastAPI 404 without our custom detail message.
    try:
        cmd_body = r_cmd.json()
    except Exception:
        cmd_body = {}
    cmd_body_str = str(cmd_body).lower()
    ctx.record(
        "route_cqrs_commands_exists",
        r_cmd.status_code in (200, 404, 405, 422)
        and (r_cmd.status_code != 404 or "handler" in cmd_body_str or "command" in cmd_body_str),
        f"POST /api/v1/cqrs/commands: {r_cmd.status_code} body={str(cmd_body)[:150]}",
    )

    r_qry = await client.post("/api/v1/cqrs/queries", json={"name": "UnknownQuery", "payload": {}})
    try:
        qry_body = r_qry.json()
    except Exception:
        qry_body = {}
    qry_body_str = str(qry_body).lower()
    ctx.record(
        "route_cqrs_queries_exists",
        r_qry.status_code in (200, 404, 405, 422)
        and (r_qry.status_code != 404 or "handler" in qry_body_str or "query" in qry_body_str),
        f"POST /api/v1/cqrs/queries: {r_qry.status_code} body={str(qry_body)[:150]}",
    )

    # Unknown command/query must return 404 (not a 500 crash)
    ctx.record(
        "unknown_command_returns_404",
        r_cmd.status_code == 404,
        f"POST /api/v1/cqrs/commands with unknown name: {r_cmd.status_code} (expected 404)",
    )

    # --- 4. CQRS_ENABLED in config ---
    cfg_path = project_dir / "app" / "core" / "config.py"
    cfg_src = cfg_path.read_text() if cfg_path.exists() else ""
    ctx.record(
        "cqrs_enabled_in_config",
        "CQRS_ENABLED" in cfg_src,
        "CQRS_ENABLED present in app/core/config.py",
    )
    ctx.record(
        "database_read_url_in_config",
        "DATABASE_READ_URL" in cfg_src,
        "DATABASE_READ_URL present in app/core/config.py",
    )


CQRS = Scenario(
    name="cqrs",
    archetype="CQRS CommandBus + QueryBus + read-replica routing",
    models={"Item": {"title": "str", "description": "text"}},
    tools=[
        ("add_cqrs", "adapt.extend.api_design.add_cqrs"),
    ],
    flow=flow_cqrs,
)


# ===========================================================================
# SCENARIO 34 — CSRF Protection + Input Sanitization
# ===========================================================================


async def flow_csrf_and_sanitization(ctx: ScenarioContext) -> None:
    """CSRF tokens + input sanitizer: classes present, endpoint works, config patched."""
    client = ctx.client
    project_dir = ctx.project_dir

    # --- 1. CSRFProtection class with generate_token / validate_token ---
    csrf_file = project_dir / "app" / "security" / "csrf.py"
    if csrf_file.exists():
        src = csrf_file.read_text()
        ctx.record(
            "csrf_protection_class_present",
            "class CSRFProtection" in src,
            "CSRFProtection class in app/security/csrf.py",
        )
        ctx.record(
            "csrf_generate_token_method",
            "def generate_token" in src,
            "generate_token method present",
        )
        ctx.record(
            "csrf_validate_token_method",
            "def validate_token" in src,
            "validate_token method present",
        )
    else:
        for label in [
            "csrf_protection_class_present",
            "csrf_generate_token_method",
            "csrf_validate_token_method",
        ]:
            ctx.record(label, False, "app/security/csrf.py not found")

    # --- 2. CSRFMiddleware present ---
    middleware_file = project_dir / "app" / "security" / "csrf_middleware.py"
    ctx.record(
        "csrf_middleware_present",
        middleware_file.exists() and "class CSRFMiddleware" in middleware_file.read_text(),
        "CSRFMiddleware class in app/security/csrf_middleware.py",
    )

    # --- 3. GET /csrf/token route FILE exists ---
    # add_csrf_protection writes app/api/routes/csrf.py but registers it with a comment
    # in main.py (manual step). We verify the route file and its endpoint definition.
    csrf_route_file = ctx.project_dir / "app" / "api" / "routes" / "csrf.py"
    if csrf_route_file.exists():
        csrf_route_src = csrf_route_file.read_text()
        ctx.record(
            "csrf_token_endpoint_exists",
            "@router.get" in csrf_route_src and "/token" in csrf_route_src,
            "GET /csrf/token route defined in app/api/routes/csrf.py",
        )
        ctx.record(
            "csrf_token_in_response",
            "csrf_token" in csrf_route_src,
            "csrf_token key referenced in csrf.py response",
        )
    else:
        ctx.record("csrf_token_endpoint_exists", False, "app/api/routes/csrf.py not found")
        ctx.record("csrf_token_in_response", False, "file not found")

    # --- 4. InputSanitizer has sanitize_html ---
    sanitizer_file = project_dir / "app" / "security" / "sanitizer.py"
    if sanitizer_file.exists():
        src = sanitizer_file.read_text()
        ctx.record(
            "input_sanitizer_class_present",
            "class InputSanitizer" in src,
            "InputSanitizer class in app/security/sanitizer.py",
        )
        ctx.record(
            "sanitize_html_method_present",
            "def sanitize_html" in src,
            "sanitize_html method in InputSanitizer",
        )
        ctx.record(
            "bleach_imported_lazily",
            # bleach must NOT appear as a top-level bare import
            not any(
                (isinstance(n, _ast.Import) and any(a.name == "bleach" for a in n.names))
                or (isinstance(n, _ast.ImportFrom) and n.module == "bleach")
                for n in _ast.parse(src).body
            ),
            "bleach not at module top-level (lazy import)",
        )
    else:
        for label in [
            "input_sanitizer_class_present",
            "sanitize_html_method_present",
            "bleach_imported_lazily",
        ]:
            ctx.record(label, False, "app/security/sanitizer.py not found")

    # --- 5. SanitizeMiddleware present ---
    smid_file = project_dir / "app" / "security" / "sanitize_middleware.py"
    ctx.record(
        "sanitize_middleware_present",
        smid_file.exists() and "class SanitizeMiddleware" in smid_file.read_text(),
        "SanitizeMiddleware class in app/security/sanitize_middleware.py",
    )

    # --- 6. SafeString validator in validators.py ---
    validators_file = project_dir / "app" / "security" / "validators.py"
    ctx.record(
        "safe_string_validator_present",
        validators_file.exists() and "SafeString" in validators_file.read_text(),
        "SafeString present in app/security/validators.py",
    )


CSRF_AND_SANITIZATION = Scenario(
    name="csrf_and_sanitization",
    archetype="CSRF double-submit cookie protection + HTML input sanitizer",
    models={"Article": {"title": "str", "content": "text"}},
    tools=[
        ("add_csrf_protection", "adapt.extend.infrastructure.add_csrf_protection"),
        ("add_input_sanitization", "adapt.extend.infrastructure.add_input_sanitization"),
    ],
    flow=flow_csrf_and_sanitization,
    needs_boot=False,  # CSRF router is generated but not auto-registered; file checks are definitive
)


# ===========================================================================
# pytest integration — one parametrized test per scenario
# ===========================================================================

SCENARIOS: list[Scenario] = [
    CQRS,
    CSRF_AND_SANITIZATION,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await _assert_scenario(scenario)
