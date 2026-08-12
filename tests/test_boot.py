"""Boot test — verifies every adapt tool produces a bootable FastAPI app.

Runs as a standalone script:
    PYTHONPATH=. python3 tests/test_boot.py

Each test case:
1. Generates a fresh base project via ``generators.orchestrator.generate_project``
2. Applies the adapt tool under test
3. Launches a subprocess: ``python -c "from app.main import app; print('BOOT OK')"``
4. Asserts "BOOT OK" appears in stdout (no ImportError / TypeError at import time)

Exit code 0  → all tools boot cleanly
Exit code 1  → one or more tools failed to boot (details printed per-tool)
"""

from __future__ import annotations

import concurrent.futures
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path

from adapt.contracts import ToolInput
from tests.common.fixture_factory import create_fixture_project


# ---------------------------------------------------------------------------
# Tool registry — (display_name, dotted_module, function_name)
# ---------------------------------------------------------------------------

EXTEND_TOOLS: list[tuple[str, str, str]] = [
    # api_design
    ("add_api_versioning",  "adapt.extend.api_design.add_api_versioning",  "add_api_versioning"),
    ("add_batch_endpoint",  "adapt.extend.api_design.add_batch_endpoint",  "add_batch_endpoint"),
    ("add_graphql",         "adapt.extend.api_design.add_graphql",         "add_graphql"),
    ("add_graphql_subscriptions","adapt.extend.api_design.add_graphql_subscriptions","add_graphql_subscriptions"),
    ("add_long_running_task","adapt.extend.api_design.add_long_running_task","add_long_running_task"),
    # auth_access
    ("add_api_key_auth",    "adapt.extend.auth_access.add_api_key_auth",   "add_api_key_auth"),
    ("add_feature_flags",   "adapt.extend.auth_access.add_feature_flags",  "add_feature_flags"),
    ("add_mfa",             "adapt.extend.auth_access.add_mfa",            "add_mfa"),
    ("add_multi_tenancy",   "adapt.extend.auth_access.add_multi_tenancy",  "add_multi_tenancy"),
    ("add_oauth2_provider", "adapt.extend.auth_access.add_oauth2_provider","add_oauth2_provider"),
    ("add_rbac",            "adapt.extend.auth_access.add_rbac",           "add_rbac"),
    # crud_data
    ("add_audit_log",       "adapt.extend.crud_data.add_audit_log",        "add_audit_log"),
    ("add_bulk_operations", "adapt.extend.crud_data.add_bulk_operations",  "add_bulk_operations"),
    ("add_cursor_pagination","adapt.extend.crud_data.add_cursor_pagination","add_cursor_pagination"),
    ("add_data_export",     "adapt.extend.crud_data.add_data_export",      "add_data_export"),
    ("add_file_upload",     "adapt.extend.crud_data.add_file_upload",      "add_file_upload"),
    ("add_search",          "adapt.extend.crud_data.add_search",           "add_search"),
    ("add_soft_delete",     "adapt.extend.crud_data.add_soft_delete",      "add_soft_delete"),
    # infrastructure
    ("add_cache_layer",      "adapt.extend.infrastructure.add_cache_layer",      "add_cache_layer"),
    ("add_circuit_breaker",  "adapt.extend.infrastructure.add_circuit_breaker",  "add_circuit_breaker"),
    ("add_outbox_pattern",   "adapt.extend.infrastructure.add_outbox_pattern",   "add_outbox_pattern"),
    ("add_saga",             "adapt.extend.infrastructure.add_saga",             "add_saga"),
    ("add_arq_worker",       "adapt.extend.infrastructure.add_arq_worker",       "add_arq_worker"),
    ("add_stripe_checkout",  "adapt.extend.infrastructure.add_stripe_checkout",  "add_stripe_checkout"),
    ("add_email_templates",  "adapt.extend.infrastructure.add_email_templates",  "add_email_templates"),
    ("add_rate_limiting",    "adapt.extend.infrastructure.add_rate_limiting",    "add_rate_limiting"),
    ("add_scheduled_tasks",  "adapt.extend.infrastructure.add_scheduled_tasks",  "add_scheduled_tasks"),
    ("add_sqladmin",         "adapt.extend.infrastructure.add_sqladmin",         "add_sqladmin"),
    ("add_celery_beat",      "adapt.extend.infrastructure.add_celery_beat",      "add_celery_beat"),
    ("add_s3_storage",       "adapt.extend.infrastructure.add_s3_storage",       "add_s3_storage"),
    ("add_health_deep",      "adapt.extend.infrastructure.add_health_deep",      "add_health_deep"),
    ("add_stripe_subscription","adapt.extend.infrastructure.add_stripe_subscription","add_stripe_subscription"),
    ("add_stripe_refund_flow","adapt.extend.infrastructure.add_stripe_refund_flow","add_stripe_refund_flow"),
    ("add_temporal_workflow","adapt.extend.infrastructure.add_temporal_workflow","add_temporal_workflow"),
    ("add_ml_model_server","adapt.extend.infrastructure.add_ml_model_server","add_ml_model_server"),
    ("add_ml_gpu_inference","adapt.extend.infrastructure.add_ml_gpu_inference","add_ml_gpu_inference"),
    ("add_ml_model_registry","adapt.extend.infrastructure.add_ml_model_registry","add_ml_model_registry"),
    ("add_notifications",    "adapt.extend.infrastructure.add_notifications",    "add_notifications"),
    # auth_access (extended)
    ("add_feature_toggles_api","adapt.extend.auth_access.add_feature_toggles_api","add_feature_toggles_api"),
    ("add_cedar_policies","adapt.extend.auth_access.add_cedar_policies","add_cedar_policies"),
    ("add_opa_integration","adapt.extend.auth_access.add_opa_integration","add_opa_integration"),
    # realtime
    ("add_sse",              "adapt.extend.realtime.add_sse",                    "add_sse"),
    ("add_webhook_receiver", "adapt.extend.realtime.add_webhook_receiver",       "add_webhook_receiver"),
    ("add_webhook_sender",   "adapt.extend.realtime.add_webhook_sender",         "add_webhook_sender"),
    ("add_websocket_chat",   "adapt.extend.realtime.add_websocket_chat",         "add_websocket_chat"),
    ("add_websocket_presence","adapt.extend.realtime.add_websocket_presence",   "add_websocket_presence"),
    # testing_tools
    ("add_contract_tests",  "adapt.extend.testing_tools.add_contract_tests","add_contract_tests"),
    ("add_factory",         "adapt.extend.testing_tools.add_factory",      "add_factory"),
    ("add_load_profile",    "adapt.extend.testing_tools.add_load_profile", "add_load_profile"),
    # NEW TOOLS (Batch 4)
    ("add_cors_config",          "adapt.extend.infrastructure.add_cors_config",            "add_cors_config"),
    ("add_cqrs",                 "adapt.extend.api_design.add_cqrs",                       "add_cqrs"),
    ("add_csrf_protection",      "adapt.extend.infrastructure.add_csrf_protection",        "add_csrf_protection"),
    ("add_data_import",          "adapt.extend.crud_data.add_data_import",                 "add_data_import"),
    ("add_data_versioning",      "adapt.extend.crud_data.add_data_versioning",             "add_data_versioning"),
    ("add_database_migrations_ci","adapt.extend.testing_tools.add_database_migrations_ci",  "add_database_migrations_ci"),
    ("add_docker_production",    "adapt.extend.infrastructure.add_docker_production",      "add_docker_production"),
    ("add_e2e_test_suite",       "adapt.extend.testing_tools.add_e2e_test_suite",          "add_e2e_test_suite"),
    ("add_event_sourcing",       "adapt.extend.crud_data.add_event_sourcing",              "add_event_sourcing"),
    ("add_excel_export",         "adapt.extend.infrastructure.add_excel_export",           "add_excel_export"),
    ("add_input_sanitization",   "adapt.extend.infrastructure.add_input_sanitization",     "add_input_sanitization"),
    ("add_kubernetes_manifests", "adapt.extend.infrastructure.add_kubernetes_manifests",   "add_kubernetes_manifests"),
    ("add_opentelemetry",        "adapt.extend.infrastructure.add_opentelemetry",          "add_opentelemetry"),
    ("add_passkey_auth",         "adapt.extend.auth_access.add_passkey_auth",              "add_passkey_auth"),
    ("add_pdf_reports",          "adapt.extend.infrastructure.add_pdf_reports",            "add_pdf_reports"),
    ("add_prometheus_metrics",   "adapt.extend.infrastructure.add_prometheus_metrics",     "add_prometheus_metrics"),
    ("add_push_notifications_native","adapt.extend.infrastructure.add_push_notifications_native","add_push_notifications_native"),
    ("add_sms_otp",              "adapt.extend.auth_access.add_sms_otp",                   "add_sms_otp"),
    ("add_social_login",         "adapt.extend.auth_access.add_social_login",              "add_social_login"),
    ("add_structured_logging",   "adapt.extend.infrastructure.add_structured_logging",     "add_structured_logging"),
    ("add_transactional_email",  "adapt.extend.infrastructure.add_transactional_email",    "add_transactional_email"),
    # BATCH 5 (30 tools)
    ("add_adaptive_throttle",    "adapt.extend.infrastructure.add_adaptive_throttle",      "add_adaptive_throttle"),
    ("add_adaptive_timeouts",    "adapt.extend.infrastructure.add_adaptive_timeouts",      "add_adaptive_timeouts"),
    ("add_anomaly_detector",     "adapt.extend.infrastructure.add_anomaly_detector",       "add_anomaly_detector"),
    ("add_api_deprecation",      "adapt.extend.api_design.add_api_deprecation",            "add_api_deprecation"),
    ("add_api_fuzzer",           "adapt.extend.testing_tools.add_api_fuzzer",              "add_api_fuzzer"),
    ("add_api_monetization",     "adapt.extend.infrastructure.add_api_monetization",       "add_api_monetization"),
    ("add_api_replay_debugger",  "adapt.extend.infrastructure.add_api_replay_debugger",    "add_api_replay_debugger"),
    ("add_bola_guard",           "adapt.extend.auth_access.add_bola_guard",                "add_bola_guard"),
    ("add_bulkhead_isolation",   "adapt.extend.infrastructure.add_bulkhead_isolation",     "add_bulkhead_isolation"),
    ("add_canary_tokens",        "adapt.extend.infrastructure.add_canary_tokens",          "add_canary_tokens"),
    ("add_chaos_testing",        "adapt.extend.infrastructure.add_chaos_testing",          "add_chaos_testing"),
    ("add_compliance_engine",    "adapt.extend.infrastructure.add_compliance_engine",      "add_compliance_engine"),
    ("add_cost_tracker",         "adapt.extend.infrastructure.add_cost_tracker",           "add_cost_tracker"),
    ("add_data_seeder",          "adapt.extend.testing_tools.add_data_seeder",             "add_data_seeder"),
    ("add_dependency_health_map", "adapt.extend.infrastructure.add_dependency_health_map",  "add_dependency_health_map"),
    ("add_dlp_shield",           "adapt.extend.infrastructure.add_dlp_shield",             "add_dlp_shield"),
    ("add_dpop_tokens",          "adapt.extend.auth_access.add_dpop_tokens",               "add_dpop_tokens"),
    ("add_graceful_shutdown",    "adapt.extend.infrastructure.add_graceful_shutdown",      "add_graceful_shutdown"),
    ("add_load_shedding",        "adapt.extend.infrastructure.add_load_shedding",          "add_load_shedding"),
    ("add_request_fingerprint",  "adapt.extend.infrastructure.add_request_fingerprint",    "add_request_fingerprint"),
    ("add_request_signing",      "adapt.extend.auth_access.add_request_signing",           "add_request_signing"),
    ("add_request_tracing_ui",   "adapt.extend.infrastructure.add_request_tracing_ui",     "add_request_tracing_ui"),
    ("add_response_armor",       "adapt.extend.infrastructure.add_response_armor",         "add_response_armor"),
    ("add_retry_budget",         "adapt.extend.infrastructure.add_retry_budget",           "add_retry_budget"),
    ("add_runtime_sentinel",     "adapt.extend.infrastructure.add_runtime_sentinel",       "add_runtime_sentinel"),
    ("add_sbom_guardian",        "adapt.extend.testing_tools.add_sbom_guardian",           "add_sbom_guardian"),
    ("add_schema_enforcer",      "adapt.extend.testing_tools.add_schema_enforcer",         "add_schema_enforcer"),
    ("add_schema_evolution_guard", "adapt.extend.testing_tools.add_schema_evolution_guard",  "add_schema_evolution_guard"),
    ("add_secret_rotation",      "adapt.extend.infrastructure.add_secret_rotation",        "add_secret_rotation"),
    ("add_tenant_onboarding",    "adapt.extend.infrastructure.add_tenant_onboarding",      "add_tenant_onboarding"),
]


# ---------------------------------------------------------------------------
# Boot runner
# ---------------------------------------------------------------------------

def _run_tool_boot_test(
    tool_name: str,
    module_path: str,
    fn_name: str,
) -> tuple[bool, str]:
    """Generate base project, apply tool, attempt boot.

    Args:
        tool_name: Human-readable tool name for error messages.
        module_path: Dotted import path for the tool module.
        fn_name: Name of the tool function to call.

    Returns:
        Tuple of (passed: bool, error_message: str).
        ``error_message`` is empty when ``passed`` is True.
    """
    try:
        mod = importlib.import_module(module_path)
        fn = getattr(mod, fn_name)
    except Exception as exc:
        return False, f"Import failed: {exc}"

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = create_fixture_project(
                name=f"boot_{tool_name}",
                tmp_dir=Path(tmp_dir),
            )
            result = fn(ToolInput(project_dir=str(project_dir)))
            if result.status == "error":
                return False, f"Tool error: {result.error}"

            r = subprocess.run(
                [sys.executable, "-c", "from app.main import app; print('BOOT OK')"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=60,  # 30 → 60 to absorb CPU contention when run in parallel
                            # with pytest_full_sweep + cross_composition (Wave I-1.R
                            # observed flakiness on add_event_sourcing + add_e2e_test_suite
                            # under load). Solo runs finish in <5s per tool typically.
            )
            if "BOOT OK" in r.stdout:
                return True, ""

            # Extract first meaningful error line from stderr
            for line in r.stderr.splitlines():
                stripped = line.strip()
                if stripped and any(
                    kw in stripped for kw in ("Error", "error", "Exception", "Traceback")
                ):
                    # Skip noise lines from pydantic/otel
                    if any(skip in stripped for skip in ("pydantic", "opentelemetry", "logfire", "UserWarning")):
                        continue
                    return False, stripped
            return False, f"returncode={r.returncode}, no error line found"

    except subprocess.TimeoutExpired:
        return False, "Boot subprocess timed out (>60s)"
    except Exception as exc:
        return False, f"Unexpected exception: {exc}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Run all boot tests and print a summary.

    Each tool's boot test is independent (own temp project, own boot
    subprocess, no shared state), so they run on a thread pool. The boot
    subprocess.run releases the GIL while it waits, so 4 workers overlap the
    ~6s-per-tool boots — turning a ~9min serial sweep into ~3min. Workers are
    capped at 4 to stay a good CPU neighbour on the shared CI mac.

    Returns:
        0 if all tools pass, 1 if any fail.
    """
    total = len(EXTEND_TOOLS)
    passed = 0
    failed: list[tuple[str, str]] = []

    print(f"Running boot tests for {total} adapt tools (parallel, 4 workers) ...\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_run_tool_boot_test, name, module_path, fn_name): name
            for name, module_path, fn_name in EXTEND_TOOLS
        }
        for fut in concurrent.futures.as_completed(futures):
            tool_name = futures[fut]
            ok, error_msg = fut.result()
            if ok:
                passed += 1
                print(f"  PASS  {tool_name}")
            else:
                failed.append((tool_name, error_msg))
                print(f"  FAIL  {tool_name} — {error_msg}")

    failed.sort()
    print(f"\n{'='*60}")
    print(f"Boot test result: {passed}/{total} tools boot cleanly")
    if failed:
        print(f"\nFailed tools ({len(failed)}):")
        for name, msg in failed:
            print(f"  {name}: {msg}")
        return 1
    print("All tools boot cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())