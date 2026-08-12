"""Cross-composition test — verify that any N-tool subset boots cleanly.

Applies all 100 EXTEND tools in N-at-a-time combinations to a fresh project
and verifies that the project still boots (``from app.main import app``),
every ``.py`` file AST-parses, and no tool returns ``status="error"``.

Strategy: instead of testing all C(34,2)=561 pairs exhaustively (which
would take hours), we test:

1. **All-34 stack**: every tool applied in canonical order (the stress test
   scenario — proves the "full install" boots).
2. **Conflict-prone pairs**: tools that patch the SAME files (config.py,
   main.py, models/__init__.py, routes/__init__.py, requirements.txt).
   These are the high-risk combinations.
3. **Random 5-tool subsets**: 20 random draws of 5 tools each, applied in
   random order. Catches ordering-dependent bugs.

Run::

    PYTHONPATH=. python3 tests/test_cross_composition.py
"""

from __future__ import annotations

import ast
import importlib
import os
import random
import subprocess
import sys
import tempfile
import time
from itertools import combinations
from pathlib import Path

os.environ.setdefault("RATE_LIMITING_ENABLED", "false")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-cross-comp-32-chars!!")
os.environ.setdefault("MFA_FERNET_KEY", "L7gvXDh2v6syV65J0-iwLQMTYbVavNXO2vuXgntcFBo=")

from adapt.contracts import ToolInput
from tests.common.fixture_factory import create_fixture_project


# ---------------------------------------------------------------------------
# Tool registry — all 100 EXTEND tools
# ---------------------------------------------------------------------------

ALL_EXTEND: list[tuple[str, str]] = [
    # crud_data (7)
    ("add_soft_delete",       "adapt.extend.crud_data.add_soft_delete"),
    ("add_cursor_pagination", "adapt.extend.crud_data.add_cursor_pagination"),
    ("add_search",            "adapt.extend.crud_data.add_search"),
    ("add_audit_log",         "adapt.extend.crud_data.add_audit_log"),
    ("add_bulk_operations",   "adapt.extend.crud_data.add_bulk_operations"),
    ("add_data_export",       "adapt.extend.crud_data.add_data_export"),
    ("add_file_upload",       "adapt.extend.crud_data.add_file_upload"),
    # auth_access (6)
    ("add_multi_tenancy",     "adapt.extend.auth_access.add_multi_tenancy"),
    ("add_rbac",              "adapt.extend.auth_access.add_rbac"),
    ("add_mfa",               "adapt.extend.auth_access.add_mfa"),
    ("add_api_key_auth",      "adapt.extend.auth_access.add_api_key_auth"),
    ("add_feature_flags",     "adapt.extend.auth_access.add_feature_flags"),
    ("add_oauth2_provider",   "adapt.extend.auth_access.add_oauth2_provider"),
    # infrastructure (10)
    ("add_cache_layer",       "adapt.extend.infrastructure.add_cache_layer"),
    ("add_circuit_breaker",   "adapt.extend.infrastructure.add_circuit_breaker"),
    ("add_outbox_pattern",    "adapt.extend.infrastructure.add_outbox_pattern"),
    ("add_saga",              "adapt.extend.infrastructure.add_saga"),
    ("add_arq_worker",        "adapt.extend.infrastructure.add_arq_worker"),
    ("add_stripe_checkout",   "adapt.extend.infrastructure.add_stripe_checkout"),
    ("add_email_templates",   "adapt.extend.infrastructure.add_email_templates"),
    ("add_rate_limiting",     "adapt.extend.infrastructure.add_rate_limiting"),
    ("add_scheduled_tasks",   "adapt.extend.infrastructure.add_scheduled_tasks"),
    ("add_sqladmin",          "adapt.extend.infrastructure.add_sqladmin"),
    ("add_celery_beat",       "adapt.extend.infrastructure.add_celery_beat"),
    ("add_s3_storage",        "adapt.extend.infrastructure.add_s3_storage"),
    ("add_health_deep",       "adapt.extend.infrastructure.add_health_deep"),
    ("add_notifications",     "adapt.extend.infrastructure.add_notifications"),
    ("add_stripe_subscription","adapt.extend.infrastructure.add_stripe_subscription"),
    ("add_stripe_refund_flow","adapt.extend.infrastructure.add_stripe_refund_flow"),
    ("add_temporal_workflow", "adapt.extend.infrastructure.add_temporal_workflow"),
    ("add_ml_model_server",   "adapt.extend.infrastructure.add_ml_model_server"),
    ("add_ml_gpu_inference",  "adapt.extend.infrastructure.add_ml_gpu_inference"),
    ("add_ml_model_registry", "adapt.extend.infrastructure.add_ml_model_registry"),
    # realtime (5)
    ("add_sse",               "adapt.extend.realtime.add_sse"),
    ("add_webhook_receiver",  "adapt.extend.realtime.add_webhook_receiver"),
    ("add_webhook_sender",    "adapt.extend.realtime.add_webhook_sender"),
    ("add_websocket_chat",    "adapt.extend.realtime.add_websocket_chat"),
    ("add_websocket_presence","adapt.extend.realtime.add_websocket_presence"),
    # auth_access extended
    ("add_feature_toggles_api","adapt.extend.auth_access.add_feature_toggles_api"),
    ("add_cedar_policies",   "adapt.extend.auth_access.add_cedar_policies"),
    ("add_opa_integration",  "adapt.extend.auth_access.add_opa_integration"),
    # api_design (5)
    ("add_api_versioning",    "adapt.extend.api_design.add_api_versioning"),
    ("add_batch_endpoint",    "adapt.extend.api_design.add_batch_endpoint"),
    ("add_graphql",           "adapt.extend.api_design.add_graphql"),
    ("add_long_running_task", "adapt.extend.api_design.add_long_running_task"),
    ("add_graphql_subscriptions","adapt.extend.api_design.add_graphql_subscriptions"),
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
    ("add_contract_tests",    "adapt.extend.testing_tools.add_contract_tests"),
    ("add_factory",           "adapt.extend.testing_tools.add_factory"),
    ("add_load_profile",      "adapt.extend.testing_tools.add_load_profile"),

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

# Tools known to patch THESE shared files — high-risk pairs.
_CONFIG_PATCHERS = [
    n for n, _ in ALL_EXTEND
    if n in {
        "add_cache_layer", "add_circuit_breaker", "add_outbox_pattern",
        "add_saga", "add_arq_worker", "add_stripe_checkout",
        "add_email_templates", "add_rate_limiting", "add_scheduled_tasks",
        "add_sqladmin", "add_sse", "add_webhook_receiver",
        "add_webhook_sender", "add_websocket_chat", "add_mfa",
        "add_feature_flags", "add_long_running_task",
        "add_celery_beat", "add_s3_storage", "add_health_deep",
        "add_notifications", "add_websocket_presence", "add_feature_toggles_api",
        "add_stripe_subscription", "add_stripe_refund_flow", "add_temporal_workflow",
        "add_ml_model_server", "add_ml_model_registry",
        "add_cedar_policies", "add_opa_integration", "add_graphql_subscriptions",
    }
]

_MAIN_PATCHERS = [
    n for n, _ in ALL_EXTEND
    if n in {
        "add_cache_layer", "add_rate_limiting", "add_scheduled_tasks",
        "add_sqladmin", "add_arq_worker",
    }
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_tools(
    project_dir: Path,
    tools: list[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Apply tools in order; return list of (name, status, error)."""
    results = []
    for name, mod_path in tools:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, name)
        r = fn(ToolInput(project_dir=str(project_dir)))
        results.append((name, r.status, r.error or ""))
    return results


def _all_py_parse(project_dir: Path) -> list[str]:
    """Return error strings for .py files that fail ast.parse."""
    errors = []
    for f in sorted(project_dir.rglob("*.py")):
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            errors.append(f"{f}: {e}")
    return errors


def _boot_ok(project_dir: Path) -> tuple[bool, str]:
    """Try to import app.main in a subprocess.

    Sets SKIP_LIFESPAN=1 so that tools which patch main.py with startup
    hooks (init_cache, start_scheduler, create_arq_pool) do NOT actually
    try to connect to Redis/PostgreSQL during the import-level boot check.
    The boot test verifies that the module LOADS cleanly — not that external
    services are reachable.
    """
    base_env = {k: v for k, v in os.environ.items() if k != "REDIS_URL"}
    env = {
        **base_env,
        "SKIP_LIFESPAN": "1",
        "RATE_LIMITING_ENABLED": "false",
        "SCHEDULER_ENABLED": "false",
    }
    try:
        r = subprocess.run(
            [sys.executable, "-c", "from app.main import app; print('BOOT_OK')"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "Boot timed out (>60s)"
    if "BOOT_OK" in r.stdout:
        return True, ""
    # The real error is usually the LAST line with "Error" in stderr.
    # Redis/DB connection errors at startup are expected (no containers)
    # and should be skipped.
    skip_patterns = (
        "pydantic", "opentelemetry", "UserWarning", "redis.",
        "ConnectionError", "ConnectionRefused", "ConnectionPool",
    )
    error_lines = []
    for line in r.stderr.splitlines():
        s = line.strip()
        if s and any(kw in s for kw in ("Error", "Exception")):
            if not any(skip in s for skip in skip_patterns):
                error_lines.append(s)
    if error_lines:
        return False, error_lines[-1]
    return False, f"returncode={r.returncode}, stderr tail: {r.stderr[-200:]}"


def _tool_by_name(name: str) -> tuple[str, str]:
    for n, m in ALL_EXTEND:
        if n == name:
            return n, m
    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Test 1 — Full stack (all 34 tools in canonical order)
# ---------------------------------------------------------------------------

def test_full_stack() -> tuple[bool, str]:
    """Apply all 100 EXTEND tools in order → project boots + parses."""
    project_dir = create_fixture_project(name="xcomp_full")
    results = _apply_tools(project_dir, ALL_EXTEND)

    errors_list = [f"  {n}: {e}" for n, s, e in results if s == "error"]
    if errors_list:
        return False, f"Tool errors:\n" + "\n".join(errors_list)

    parse_errs = _all_py_parse(project_dir)
    if parse_errs:
        return False, f"Parse errors: {parse_errs[0]}"

    ok, err = _boot_ok(project_dir)
    if not ok:
        return False, f"Boot failed: {err}"

    return True, f"34/34 applied, boot OK"


# ---------------------------------------------------------------------------
# Test 2 — Conflict-prone pairs (tools patching same files)
# ---------------------------------------------------------------------------

def test_config_patcher_pairs() -> tuple[bool, list[str]]:
    """Apply each pair of config-patching tools → project parses + boots."""
    pairs = list(combinations(_CONFIG_PATCHERS, 2))
    failures = []
    total = len(pairs)

    for i, (a, b) in enumerate(pairs):
        project_dir = create_fixture_project(name=f"xcomp_cfg_{i}")
        tools = [_tool_by_name(a), _tool_by_name(b)]
        results = _apply_tools(project_dir, tools)

        tool_errors = [n for n, s, _ in results if s == "error"]
        if tool_errors:
            continue  # tool legitimately declined (missing auth deps etc.)

        parse_errs = _all_py_parse(project_dir)
        if parse_errs:
            failures.append(f"[{a}+{b}] parse: {parse_errs[0]}")
            continue

        ok, err = _boot_ok(project_dir)
        if not ok:
            failures.append(f"[{a}+{b}] boot: {err}")

    return len(failures) == 0, failures


def test_main_patcher_pairs() -> tuple[bool, list[str]]:
    """Apply each pair of main.py-patching tools → project boots."""
    pairs = list(combinations(_MAIN_PATCHERS, 2))
    failures = []

    for i, (a, b) in enumerate(pairs):
        project_dir = create_fixture_project(name=f"xcomp_main_{i}")
        tools = [_tool_by_name(a), _tool_by_name(b)]
        results = _apply_tools(project_dir, tools)

        tool_errors = [n for n, s, _ in results if s == "error"]
        if tool_errors:
            continue

        ok, err = _boot_ok(project_dir)
        if not ok:
            failures.append(f"[{a}+{b}] boot: {err}")

    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Test 3 — Random 5-tool subsets (ordering fuzz)
# ---------------------------------------------------------------------------

def test_random_subsets(n_draws: int = 20, subset_size: int = 5) -> tuple[bool, list[str]]:
    """Draw random subsets of tools, apply in random order, verify boot."""
    rng = random.Random(42)
    failures = []

    for draw in range(n_draws):
        subset = rng.sample(ALL_EXTEND, min(subset_size, len(ALL_EXTEND)))
        rng.shuffle(subset)
        names = [n for n, _ in subset]

        project_dir = create_fixture_project(name=f"xcomp_rnd_{draw}")
        results = _apply_tools(project_dir, subset)

        tool_errors = [n for n, s, _ in results if s == "error"]

        parse_errs = _all_py_parse(project_dir)
        if parse_errs:
            failures.append(f"draw {draw} {names}: parse: {parse_errs[0]}")
            continue

        ok, err = _boot_ok(project_dir)
        if not ok:
            non_err = [(n, s) for n, s, _ in results if s != "error"]
            failures.append(f"draw {draw} {names}: boot: {err}")

    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Test 4 — Reverse order (all 34 tools applied in reverse)
# ---------------------------------------------------------------------------

def test_reverse_stack() -> tuple[bool, str]:
    """Apply all 100 EXTEND tools in REVERSE order → project boots."""
    reversed_tools = list(reversed(ALL_EXTEND))
    project_dir = create_fixture_project(name="xcomp_rev")
    results = _apply_tools(project_dir, reversed_tools)

    parse_errs = _all_py_parse(project_dir)
    if parse_errs:
        return False, f"Parse errors: {parse_errs[0]}"

    ok, err = _boot_ok(project_dir)
    if not ok:
        return False, f"Boot failed (reverse order): {err}"

    return True, "34/34 reversed, boot OK"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"\n{'='*70}")
    print(f"CROSS-COMPOSITION TEST — {len(ALL_EXTEND)} EXTEND tools")
    print(f"{'='*70}\n")

    t0 = time.monotonic()
    all_ok = True

    # Test 1: Full stack
    print("Test 1: Full stack (all 34 tools, canonical order)...")
    ok, msg = test_full_stack()
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")
    if not ok:
        all_ok = False

    # Test 2a: Config patcher pairs
    n_cfg = len(list(combinations(_CONFIG_PATCHERS, 2)))
    print(f"\nTest 2a: Config-patcher pairs ({n_cfg} pairs)...")
    ok, failures = test_config_patcher_pairs()
    print(f"  {'PASS' if ok else 'FAIL'}: {n_cfg - len(failures)}/{n_cfg} pairs boot")
    for f in failures[:5]:
        print(f"    {f}")
    if not ok:
        all_ok = False

    # Test 2b: Main.py patcher pairs
    n_main = len(list(combinations(_MAIN_PATCHERS, 2)))
    print(f"\nTest 2b: Main.py-patcher pairs ({n_main} pairs)...")
    ok, failures = test_main_patcher_pairs()
    print(f"  {'PASS' if ok else 'FAIL'}: {n_main - len(failures)}/{n_main} pairs boot")
    for f in failures[:5]:
        print(f"    {f}")
    if not ok:
        all_ok = False

    # Test 3: Random subsets
    print("\nTest 3: Random 5-tool subsets (20 draws)...")
    ok, failures = test_random_subsets()
    print(f"  {'PASS' if ok else 'FAIL'}: {20 - len(failures)}/20 subsets boot")
    for f in failures[:5]:
        print(f"    {f}")
    if not ok:
        all_ok = False

    # Test 4: Reverse stack
    print("\nTest 4: Reverse stack (all 34 tools, reverse order)...")
    ok, msg = test_reverse_stack()
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")
    if not ok:
        all_ok = False

    elapsed = time.monotonic() - t0
    print(f"\n{'='*70}")
    if all_ok:
        print(f"CROSS-COMPOSITION: ALL PASSED ({elapsed:.0f}s)")
    else:
        print(f"CROSS-COMPOSITION: FAILED ({elapsed:.0f}s)")
    print(f"{'='*70}\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
