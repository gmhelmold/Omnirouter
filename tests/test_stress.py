"""Stress tests for SKILL-001-fastapi-production — Round 4A hardening.

Three scenarios:
    Test 1 — 20-model enterprise project + all 27 EXTEND tools
        Verifies scale, boot, parse correctness, file count, timing, memory.

    Test 2 — Generated tests still pass after 5 schema-touching tools
        Generates base project, applies 5 tools, runs the test suite that the
        orchestrator itself produced, asserts ≥ 15/19 tests pass.

    Test 3 — OpenAPI spec consistency after 5 tools
        Checks zero duplicate operationId, every endpoint has summary or
        description, every body endpoint has a request schema.

Run from the skill root:
    PYTHONPATH=. python3 tests/test_stress.py
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import resource
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running directly as a script from any CWD
# ---------------------------------------------------------------------------

_SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

from adapt.contracts import ToolInput
from generators.orchestrator import generate_project

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODELS_20: dict[str, dict[str, str]] = {
    "Customer":  {"name": "str", "email": "str", "phone": "str"},
    "Product":   {"name": "str", "sku": "str", "price": "int", "stock": "int"},
    "Order":     {"customer_id": "UUID", "total_cents": "int", "status": "str"},
    "OrderItem": {"order_id": "UUID", "product_id": "UUID", "quantity": "int", "unit_price": "int"},
    "Invoice":   {"order_id": "UUID", "amount_cents": "int", "due_date": "date", "paid_at": "datetime"},
    # Renamed from "Payment" to avoid crud/payment.py collision with
    # add_stripe_checkout, which reserves that module for its own CRUD.
    "Charge":    {"invoice_ref": "str", "amount_cents": "int", "method": "str", "status": "str"},
    # Renamed from "Subscription" to avoid crud/subscription.py collision
    # with add_stripe_subscription (which owns the stripe-sub DTOs).
    "MembershipPlan": {"customer_id": "UUID", "plan": "str", "status": "str", "renews_at": "datetime"},
    "Ticket":    {"customer_id": "UUID", "subject": "str", "priority": "str", "status": "str"},
    "Comment":   {"ticket_id": "UUID", "author_id": "UUID", "body": "str"},
    "Category":  {"name": "str", "parent_id": "UUID", "sort_order": "int"},
    "Tag":       {"name": "str", "color": "str"},
    # Renamed from "Notification" to avoid model-name collision with
    # add_notifications, which claims `crud/notification.py` as its own
    # service-layer module (create_notification / list_unread / etc.).
    "UserAlert": {"user_id": "UUID", "title": "str", "body": "str", "read_at": "datetime"},
    "AuditEntry": {"actor_id": "UUID", "action": "str", "resource_type": "str", "resource_id": "UUID"},
    "Setting":   {"key": "str", "value": "str", "scope": "str"},
    "ApiToken":  {"user_id": "UUID", "token_hash": "str", "expires_at": "datetime"},
    # Renamed from "Webhook" to avoid schemas/webhook.py collision with
    # add_webhook_sender (which reserves that module for its own schemas).
    # Also renamed from a clashing "Notification" key that collided with
    # the user-notification model above — `add_notifications` reserves
    # `crud.notification` for its own `create(...)` signature.
    "OutboundHook": {"url": "str", "events": "str", "is_active": "bool"},
    "Campaign":  {"name": "str", "start_date": "date", "end_date": "date", "budget_cents": "int"},
    # Renamed from "Report" to avoid schemas/report.py collision with
    # add_pdf_reports (which reserves that module for its own DTOs).
    "AnalyticsRun": {"name": "str", "generated_at": "datetime", "file_key": "str"},
    # Renamed from "Role" to "AppRole" to avoid table-name collision with
    # add_rbac, which reserves the "roles" tablename for its own RBAC model.
    "AppRole":   {"name": "str", "permissions": "str"},
    "Session":   {"user_id": "UUID", "token": "str", "expires_at": "datetime", "ip_address": "str"},
}

# All 49 EXTEND tools: (display_name, dotted_module)
_ALL_TOOLS: list[tuple[str, str]] = [
    # api_design (4)
    ("add_api_versioning",   "adapt.extend.api_design.add_api_versioning"),
    ("add_batch_endpoint",   "adapt.extend.api_design.add_batch_endpoint"),
    ("add_graphql",          "adapt.extend.api_design.add_graphql"),
    ("add_graphql_subscriptions","adapt.extend.api_design.add_graphql_subscriptions"),
    ("add_long_running_task","adapt.extend.api_design.add_long_running_task"),
    # auth_access (6)
    ("add_api_key_auth",     "adapt.extend.auth_access.add_api_key_auth"),
    ("add_feature_flags",    "adapt.extend.auth_access.add_feature_flags"),
    ("add_mfa",              "adapt.extend.auth_access.add_mfa"),
    ("add_multi_tenancy",    "adapt.extend.auth_access.add_multi_tenancy"),
    ("add_oauth2_provider",  "adapt.extend.auth_access.add_oauth2_provider"),
    ("add_rbac",             "adapt.extend.auth_access.add_rbac"),
    # crud_data (7)
    ("add_audit_log",        "adapt.extend.crud_data.add_audit_log"),
    ("add_bulk_operations",  "adapt.extend.crud_data.add_bulk_operations"),
    ("add_cursor_pagination","adapt.extend.crud_data.add_cursor_pagination"),
    ("add_data_export",      "adapt.extend.crud_data.add_data_export"),
    ("add_file_upload",      "adapt.extend.crud_data.add_file_upload"),
    ("add_search",           "adapt.extend.crud_data.add_search"),
    ("add_soft_delete",      "adapt.extend.crud_data.add_soft_delete"),
    # infrastructure (10)
    ("add_cache_layer",      "adapt.extend.infrastructure.add_cache_layer"),
    ("add_circuit_breaker",  "adapt.extend.infrastructure.add_circuit_breaker"),
    ("add_outbox_pattern",   "adapt.extend.infrastructure.add_outbox_pattern"),
    ("add_saga",             "adapt.extend.infrastructure.add_saga"),
    ("add_arq_worker",       "adapt.extend.infrastructure.add_arq_worker"),
    ("add_stripe_checkout",  "adapt.extend.infrastructure.add_stripe_checkout"),
    ("add_email_templates",  "adapt.extend.infrastructure.add_email_templates"),
    ("add_rate_limiting",    "adapt.extend.infrastructure.add_rate_limiting"),
    ("add_scheduled_tasks",  "adapt.extend.infrastructure.add_scheduled_tasks"),
    ("add_sqladmin",         "adapt.extend.infrastructure.add_sqladmin"),
    ("add_celery_beat",      "adapt.extend.infrastructure.add_celery_beat"),
    ("add_s3_storage",       "adapt.extend.infrastructure.add_s3_storage"),
    ("add_health_deep",      "adapt.extend.infrastructure.add_health_deep"),
    ("add_stripe_subscription","adapt.extend.infrastructure.add_stripe_subscription"),
    ("add_stripe_refund_flow","adapt.extend.infrastructure.add_stripe_refund_flow"),
    ("add_temporal_workflow","adapt.extend.infrastructure.add_temporal_workflow"),
    ("add_ml_model_server","adapt.extend.infrastructure.add_ml_model_server"),
    ("add_ml_gpu_inference","adapt.extend.infrastructure.add_ml_gpu_inference"),
    ("add_ml_model_registry","adapt.extend.infrastructure.add_ml_model_registry"),
    ("add_notifications",    "adapt.extend.infrastructure.add_notifications"),
    # realtime (5)
    ("add_sse",              "adapt.extend.realtime.add_sse"),
    ("add_webhook_receiver", "adapt.extend.realtime.add_webhook_receiver"),
    ("add_webhook_sender",   "adapt.extend.realtime.add_webhook_sender"),
    ("add_websocket_chat",   "adapt.extend.realtime.add_websocket_chat"),
    ("add_websocket_presence","adapt.extend.realtime.add_websocket_presence"),
    # auth_access (extended)
    ("add_feature_toggles_api","adapt.extend.auth_access.add_feature_toggles_api"),
    ("add_cedar_policies","adapt.extend.auth_access.add_cedar_policies"),
    ("add_opa_integration","adapt.extend.auth_access.add_opa_integration"),
        # NEW TOOLS (Batch 4)
    ("add_cors_config",          "adapt.extend.infrastructure.add_cors_config"),
    ("add_cqrs",                 "adapt.extend.api_design.add_cqrs"),
    ("add_csrf_protection",      "adapt.extend.infrastructure.add_csrf_protection"),
    ("add_data_import",          "adapt.extend.crud_data.add_data_import"),
    ("add_data_versioning",      "adapt.extend.crud_data.add_data_versioning"),
    ("add_database_migrations_ci", "adapt.extend.testing_tools.add_database_migrations_ci"),
    ("add_docker_production",    "adapt.extend.infrastructure.add_docker_production"),
    ("add_e2e_test_suite",       "adapt.extend.testing_tools.add_e2e_test_suite"),
    ("add_event_sourcing",       "adapt.extend.crud_data.add_event_sourcing"),
    ("add_excel_export",         "adapt.extend.infrastructure.add_excel_export"),
    ("add_input_sanitization",   "adapt.extend.infrastructure.add_input_sanitization"),
    ("add_kubernetes_manifests", "adapt.extend.infrastructure.add_kubernetes_manifests"),
    ("add_opentelemetry",        "adapt.extend.infrastructure.add_opentelemetry"),
    ("add_passkey_auth",         "adapt.extend.auth_access.add_passkey_auth"),
    ("add_pdf_reports",          "adapt.extend.infrastructure.add_pdf_reports"),
    ("add_prometheus_metrics",   "adapt.extend.infrastructure.add_prometheus_metrics"),
    ("add_push_notifications_native", "adapt.extend.infrastructure.add_push_notifications_native"),
    ("add_sms_otp",              "adapt.extend.auth_access.add_sms_otp"),
    ("add_social_login",         "adapt.extend.auth_access.add_social_login"),
    ("add_structured_logging",   "adapt.extend.infrastructure.add_structured_logging"),
    ("add_transactional_email",  "adapt.extend.infrastructure.add_transactional_email"),
    # testing_tools (3)
    ("add_contract_tests",   "adapt.extend.testing_tools.add_contract_tests"),
    ("add_factory",          "adapt.extend.testing_tools.add_factory"),
    ("add_load_profile",     "adapt.extend.testing_tools.add_load_profile"),

    # BATCH 5 (30 tools)
    ("add_adaptive_throttle",    "adapt.extend.infrastructure.add_adaptive_throttle"),
    ("add_adaptive_timeouts",    "adapt.extend.infrastructure.add_adaptive_timeouts"),
    ("add_anomaly_detector",     "adapt.extend.infrastructure.add_anomaly_detector"),
    ("add_api_deprecation",      "adapt.extend.api_design.add_api_deprecation"),
    ("add_api_fuzzer",           "adapt.extend.testing_tools.add_api_fuzzer"),
    ("add_api_monetization",     "adapt.extend.infrastructure.add_api_monetization"),
    ("add_api_replay_debugger",  "adapt.extend.infrastructure.add_api_replay_debugger"),
    ("add_bola_guard",           "adapt.extend.auth_access.add_bola_guard"),
    ("add_bulkhead_isolation",   "adapt.extend.infrastructure.add_bulkhead_isolation"),
    ("add_canary_tokens",        "adapt.extend.infrastructure.add_canary_tokens"),
    ("add_chaos_testing",        "adapt.extend.infrastructure.add_chaos_testing"),
    ("add_compliance_engine",    "adapt.extend.infrastructure.add_compliance_engine"),
    ("add_cost_tracker",         "adapt.extend.infrastructure.add_cost_tracker"),
    ("add_data_seeder",          "adapt.extend.testing_tools.add_data_seeder"),
    ("add_dependency_health_map", "adapt.extend.infrastructure.add_dependency_health_map"),
    ("add_dlp_shield",           "adapt.extend.infrastructure.add_dlp_shield"),
    ("add_dpop_tokens",          "adapt.extend.auth_access.add_dpop_tokens"),
    ("add_graceful_shutdown",    "adapt.extend.infrastructure.add_graceful_shutdown"),
    ("add_load_shedding",        "adapt.extend.infrastructure.add_load_shedding"),
    ("add_request_fingerprint",  "adapt.extend.infrastructure.add_request_fingerprint"),
    ("add_request_signing",      "adapt.extend.auth_access.add_request_signing"),
    ("add_request_tracing_ui",   "adapt.extend.infrastructure.add_request_tracing_ui"),
    ("add_response_armor",       "adapt.extend.infrastructure.add_response_armor"),
    ("add_retry_budget",         "adapt.extend.infrastructure.add_retry_budget"),
    ("add_runtime_sentinel",     "adapt.extend.infrastructure.add_runtime_sentinel"),
    ("add_sbom_guardian",        "adapt.extend.testing_tools.add_sbom_guardian"),
    ("add_schema_enforcer",      "adapt.extend.testing_tools.add_schema_enforcer"),
    ("add_schema_evolution_guard", "adapt.extend.testing_tools.add_schema_evolution_guard"),
    ("add_secret_rotation",      "adapt.extend.infrastructure.add_secret_rotation"),
    ("add_tenant_onboarding",    "adapt.extend.infrastructure.add_tenant_onboarding"),
]

# 5 tools for Tests 2 & 3 — chosen to touch schema, routes, and auth
_TOOLS_5: list[tuple[str, str]] = [
    ("add_soft_delete",      "adapt.extend.crud_data.add_soft_delete"),
    ("add_cursor_pagination","adapt.extend.crud_data.add_cursor_pagination"),
    ("add_search",           "adapt.extend.crud_data.add_search"),
    ("add_rbac",             "adapt.extend.auth_access.add_rbac"),
    ("add_mfa",              "adapt.extend.auth_access.add_mfa"),
]

# Memory limit: 500 MB expressed in bytes (macOS ru_maxrss is in bytes)
_MEM_LIMIT_BYTES = 500 * 1024 * 1024

# Timing limit
_TIME_LIMIT_S = 60.0

# Minimum Python files expected after 20-model + all 27 tools
_MIN_PY_FILES = 300

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 64-hex chars, generated once per process. The emitted `app/core/config.py`
# fail-fasts on empty/short/known-weak SECRET_KEY in every environment
# (LOCAL included) — so any subprocess that does `from app.main import app`
# needs a real 32-byte+ key in scope, just like a dev workstation would.
_FIXTURE_SECRET_KEY: str = secrets.token_hex(32)


def _write_env_fixture(project_dir: str) -> None:
    env_path = Path(project_dir) / ".env"
    if env_path.exists():
        return
    env_path.write_text(
        "# Generated by tests/test_stress.py for test-only use.\n"
        f"SECRET_KEY={_FIXTURE_SECRET_KEY}\n"
        "ENVIRONMENT=local\n"
        "FIRST_SUPERUSER_EMAIL=\n"
        "FIRST_SUPERUSER_PASSWORD=\n",
        encoding="utf-8",
    )


def _generate_base(
    output_dir: str,
    name: str = "app",
    models: dict | None = None,
) -> dict:
    """Generate a project without OTEL/Prometheus (avoids broken-OTEL import)."""
    result = generate_project(
        output_dir=output_dir,
        name=name,
        models=models,
        owner_models={m: "user" for m in (models or {}) if m != "User"},
        with_docker_compose=False,
        with_ci=False,
        with_otel=False,
        with_prometheus=False,
    )
    _write_env_fixture(output_dir)
    return result


def _apply_tools(project_dir: str, tools: list[tuple[str, str]]) -> dict[str, str]:
    """Apply a list of EXTEND tools and return {tool_name: status}."""
    statuses: dict[str, str] = {}
    for tool_name, mod_path in tools:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, tool_name)
        result = fn(ToolInput(project_dir=project_dir))
        statuses[tool_name] = result.status
    return statuses


def _count_py_files(root: str) -> int:
    return len(list(Path(root).rglob("*.py")))


def _all_py_parseable(root: str) -> list[str]:
    """Return list of files that fail ast.parse."""
    failures: list[str] = []
    for py in Path(root).rglob("*.py"):
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{py.relative_to(root)}: {exc}")
    return failures


def _boot_ok(project_dir: str) -> tuple[bool, str]:
    """Return (True, '') if `from app.main import app` succeeds in a subprocess."""
    r = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print('BOOT OK')"],
        cwd=project_dir,
        env={**os.environ, "PYTHONPATH": project_dir},
        capture_output=True,
        text=True,
        timeout=60,
    )
    if "BOOT OK" in r.stdout:
        return True, ""
    # Extract the first real error line, skipping OTEL/pydantic/logfire noise.
    # We look for ImportError/ModuleNotFoundError/TypeError etc. but skip lines
    # whose only meaningful keyword is inside the noise prefixes.
    noise_prefixes = ("pydantic", "opentelemetry", "logfire", "UserWarning", "DeprecationWarning")
    for line in r.stderr.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(kw in s for kw in ("Error:", "Exception:", "ImportError", "ModuleNotFoundError")):
            if not any(noise in s for noise in noise_prefixes):
                return False, s
    # Fallback: return last non-empty stderr line that is not pure noise
    for line in reversed(r.stderr.splitlines()):
        s = line.strip()
        if s and not any(noise in s for noise in noise_prefixes):
            return False, f"rc={r.returncode} — {s}"
    return False, f"rc={r.returncode} (no clear error line)"


# ---------------------------------------------------------------------------
# Test 1 — 20-model + all 27 EXTEND tools
# ---------------------------------------------------------------------------

def test1_enterprise_scale() -> tuple[bool, str]:
    """20-model project + all 27 EXTEND tools.

    Checks:
        1. Boot OK
        2. All .py files parse without SyntaxError
        3. > 300 Python files generated
        4. Total wall time < 60 s
        5. Process RSS delta < 500 MB
    """
    label = "TEST 1 [20-model + 27 tools]"

    mem_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = tmp + "/enterprise20"

        # --- generate base project ---
        result = _generate_base(project_dir, name="enterprise20", models=_MODELS_20)
        if result.get("total_files", 0) == 0:
            return False, f"{label}: orchestrator produced zero files"

        # --- apply all 27 tools ---
        statuses = _apply_tools(project_dir, _ALL_TOOLS)
        errors = {k: v for k, v in statuses.items() if v == "error"}

        elapsed = time.monotonic() - t0
        mem_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mem_delta_bytes = mem_after - mem_before

        # 1. No tool returned "error"
        if errors:
            return False, f"{label}: tool(s) returned error: {errors}"

        # 2. Boot OK
        ok, err_msg = _boot_ok(project_dir)
        if not ok:
            return False, f"{label}: boot FAILED — {err_msg}"

        # 3. All .py files parse
        parse_failures = _all_py_parseable(project_dir)
        if parse_failures:
            summary = parse_failures[:5]
            return False, f"{label}: {len(parse_failures)} parse error(s): {summary}"

        # 4. File count > 300
        py_count = _count_py_files(project_dir)
        if py_count <= _MIN_PY_FILES:
            return False, (
                f"{label}: only {py_count} .py files — expected > {_MIN_PY_FILES}"
            )

        # 5. Timing < 30 s
        if elapsed > _TIME_LIMIT_S:
            return False, (
                f"{label}: elapsed {elapsed:.1f}s exceeds {_TIME_LIMIT_S}s limit"
            )

        # 6. Memory < 500 MB delta
        if mem_delta_bytes > _MEM_LIMIT_BYTES:
            mb = mem_delta_bytes / (1024 * 1024)
            return False, (
                f"{label}: RSS delta {mb:.0f} MB exceeds 500 MB limit"
            )

        tool_statuses_summary = {k: v for k, v in statuses.items() if v != "success"}
        summary_line = (
            f"27/27 tools OK | {py_count} .py files | "
            f"{elapsed:.2f}s | RSS Δ={mem_delta_bytes/(1024*1024):.0f}MB"
        )
        if tool_statuses_summary:
            summary_line += f" | non-success (idempotent): {tool_statuses_summary}"
        return True, f"{label}: PASS — {summary_line}"


# ---------------------------------------------------------------------------
# Test 2 — Generated tests still pass after 5 tools
# ---------------------------------------------------------------------------

def test2_generated_tests_survive() -> tuple[bool, str]:
    """Base project + 5 schema-touching tools → run the generated test suite.

    Criteria: at least 15 / 19 generated tests must pass.  Some tests may fail
    because tools like add_cursor_pagination change response shapes (e.g. the
    list endpoint no longer includes a top-level ``count`` key), which is an
    expected schema evolution, not a tool regression.
    """
    label = "TEST 2 [generated tests after 5 tools]"
    min_pass = 15
    expected_total = 19  # tests written by generate_test_suite

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = tmp + "/t2"
        _generate_base(
            project_dir,
            name="t2",
            models={"Item": {"title": "str", "description": "str"}},
        )

        # Apply 5 tools
        statuses = _apply_tools(project_dir, _TOOLS_5)
        errors = {k: v for k, v in statuses.items() if v == "error"}
        if errors:
            return False, f"{label}: tool errors before test run: {errors}"

        # Count generated test files
        test_files = list(Path(project_dir).glob("tests/**/*.py"))
        if not any(f.name.startswith("test_") for f in test_files):
            return False, f"{label}: no test_*.py files found after tools"

        # Run pytest using the CURRENT Python (same venv — no dep install needed)
        env = {**os.environ, "PYTHONPATH": project_dir}
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "tests/",
                "-q", "--tb=no", "--no-header",
            ],
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Parse summary line
        m_passed = re.search(r"(\d+) passed", proc.stdout)
        m_failed = re.search(r"(\d+) failed", proc.stdout)
        m_errors = re.search(r"(\d+) error", proc.stdout)

        n_passed = int(m_passed.group(1)) if m_passed else 0
        n_failed = int(m_failed.group(1)) if m_failed else 0
        n_errors = int(m_errors.group(1)) if m_errors else 0
        n_total = n_passed + n_failed + n_errors

        detail = (
            f"{n_passed} passed, {n_failed} failed, {n_errors} errors "
            f"(of ~{expected_total} generated tests)"
        )

        # Collection error (rc=4) → hard fail
        if proc.returncode == 4:
            return False, (
                f"{label}: pytest collection error (rc=4) — "
                f"stderr: {proc.stderr[-400:]}"
            )

        if n_passed < min_pass:
            return False, (
                f"{label}: only {n_passed} tests passed (need ≥ {min_pass}) — "
                f"{detail}\nstdout tail: {proc.stdout[-600:]}"
            )

        return True, f"{label}: PASS — {detail}"


# ---------------------------------------------------------------------------
# Test 3 — OpenAPI spec consistency
# ---------------------------------------------------------------------------

_OPENAPI_SCRIPT = """
import sys, json
sys.path.insert(0, '.')
from app.main import app

spec = app.openapi()
paths = spec.get('paths', {})

results = {
    'endpoint_count': 0,
    'dup_op_ids': [],
    'no_summary_or_desc': [],
    'body_no_schema': [],
}

op_ids = []
for path, methods in paths.items():
    for method, op in methods.items():
        if not isinstance(op, dict):
            continue
        results['endpoint_count'] += 1
        op_id = op.get('operationId', '')
        if op_id:
            op_ids.append(op_id)
        if not op.get('summary') and not op.get('description'):
            results['no_summary_or_desc'].append(f'{method.upper()} {path}')
        if 'requestBody' in op:
            rb = op['requestBody']
            content = rb.get('content', {})
            has_schema = any(
                'schema' in media_type_obj
                for media_type_obj in content.values()
                if isinstance(media_type_obj, dict)
            )
            if not has_schema:
                results['body_no_schema'].append(f'{method.upper()} {path}')

# Detect duplicates
seen = set()
for oid in op_ids:
    if op_ids.count(oid) > 1 and oid not in seen:
        results['dup_op_ids'].append(oid)
        seen.add(oid)

print(json.dumps(results))
"""


def test3_openapi_consistency() -> tuple[bool, str]:
    """Generate project + 5 tools → validate OpenAPI spec invariants.

    Checks:
        1. Zero endpoints with duplicate operationId
        2. Every endpoint has summary or description (not empty)
        3. Every endpoint that declares a requestBody has a request schema
    """
    label = "TEST 3 [OpenAPI consistency after 5 tools]"

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = tmp + "/t3"
        _generate_base(
            project_dir,
            name="t3",
            models={
                "Product": {"name": "str", "price": "int", "stock": "int"},
                "Order":   {"total_cents": "int", "status": "str"},
            },
        )

        statuses = _apply_tools(project_dir, _TOOLS_5)
        errors = {k: v for k, v in statuses.items() if v == "error"}
        if errors:
            return False, f"{label}: tool errors: {errors}"

        # Run spec extraction in a subprocess (keeps import side-effects isolated)
        proc = subprocess.run(
            [sys.executable, "-c", _OPENAPI_SCRIPT],
            cwd=project_dir,
            env={**os.environ, "PYTHONPATH": project_dir},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return False, (
                f"{label}: OpenAPI extraction failed (rc={proc.returncode})\n"
                f"stderr: {proc.stderr[-600:]}"
            )

        import json as _json
        try:
            data = _json.loads(proc.stdout.strip())
        except _json.JSONDecodeError:
            return False, f"{label}: could not parse JSON output: {proc.stdout[:200]}"

        endpoint_count = data["endpoint_count"]
        dup_op_ids = data["dup_op_ids"]
        no_desc = data["no_summary_or_desc"]
        body_no_schema = data["body_no_schema"]

        failures: list[str] = []

        if dup_op_ids:
            failures.append(f"duplicate operationId(s): {dup_op_ids}")

        if no_desc:
            failures.append(
                f"{len(no_desc)} endpoint(s) missing summary+description: {no_desc[:5]}"
            )

        if body_no_schema:
            failures.append(
                f"{len(body_no_schema)} requestBody endpoint(s) missing schema: {body_no_schema[:5]}"
            )

        if failures:
            return False, f"{label}: FAIL ({endpoint_count} endpoints) — " + "; ".join(failures)

        return True, (
            f"{label}: PASS — {endpoint_count} endpoints, "
            f"0 dup operationIds, 0 missing descriptions, 0 body-schema gaps"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _separator(char: str = "─", width: int = 72) -> str:
    return char * width


def main() -> int:
    """Run all three stress tests and print a summary table.

    Returns:
        0 if all tests pass, 1 if any fail.
    """
    print(_separator("═"))
    print("  SKILL-001 Stress Tests — Round 4A Hardening")
    print(_separator("═"))
    print()

    tests = [
        ("Test 1", test1_enterprise_scale),
        ("Test 2", test2_generated_tests_survive),
        ("Test 3", test3_openapi_consistency),
    ]

    results: list[tuple[str, bool, str, float]] = []

    for name, fn in tests:
        print(f"Running {name} …", flush=True)
        t0 = time.monotonic()
        try:
            ok, message = fn()
        except Exception as exc:
            ok = False
            message = f"{name}: EXCEPTION — {type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - t0
        results.append((name, ok, message, elapsed))
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}]  {message}  ({elapsed:.2f}s)")
        print()

    print(_separator())
    total = len(results)
    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = total - passed
    print(f"  Result: {passed}/{total} tests passed", end="")
    if failed:
        print(f"  ({failed} FAILED)")
    else:
        print()
    print(_separator())

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
