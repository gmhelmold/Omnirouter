"""Shared infrastructure for property_tests.

Tool registry, low-level helpers, and the _Results accumulator used by all
property modules. Split out of property_tests.py to stay under the 500-LOC cap.
No behaviour change — pure extraction.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
from collections.abc import Callable
from pathlib import Path

from adapt.contracts import ToolInput, ToolResult

# ---------------------------------------------------------------------------
# Tool registry — maps module.function to the callable, with default kwargs
# ---------------------------------------------------------------------------

# Each entry: (dotted_module, function_name, extra_kwargs_for_plain_call)
# extra_kwargs are the minimum required to not error for structural reasons
# (e.g., tools that need a non-empty `target` or `operation`).
_TOOL_REGISTRY: list[tuple[str, str, dict]] = [
    # extend / api_design
    ("adapt.extend.api_design.add_api_versioning", "add_api_versioning", {}),
    ("adapt.extend.api_design.add_batch_endpoint", "add_batch_endpoint", {}),
    ("adapt.extend.api_design.add_graphql", "add_graphql", {}),
    ("adapt.extend.api_design.add_long_running_task", "add_long_running_task", {}),
    # extend / auth_access
    ("adapt.extend.auth_access.add_api_key_auth", "add_api_key_auth", {}),
    ("adapt.extend.auth_access.add_feature_flags", "add_feature_flags", {}),
    ("adapt.extend.auth_access.add_mfa", "add_mfa", {}),
    ("adapt.extend.auth_access.add_multi_tenancy", "add_multi_tenancy", {}),
    ("adapt.extend.auth_access.add_oauth2_provider", "add_oauth2_provider", {}),
    ("adapt.extend.auth_access.add_rbac", "add_rbac", {}),
    # extend / crud_data
    ("adapt.extend.crud_data.add_audit_log", "add_audit_log", {}),
    ("adapt.extend.crud_data.add_bulk_operations", "add_bulk_operations", {}),
    ("adapt.extend.crud_data.add_cursor_pagination", "add_cursor_pagination", {}),
    ("adapt.extend.crud_data.add_data_export", "add_data_export", {}),
    ("adapt.extend.crud_data.add_file_upload", "add_file_upload", {}),
    ("adapt.extend.crud_data.add_search", "add_search", {}),
    ("adapt.extend.crud_data.add_soft_delete", "add_soft_delete", {}),
    # extend / infrastructure
    ("adapt.extend.infrastructure.add_cache_layer", "add_cache_layer", {}),
    ("adapt.extend.infrastructure.add_circuit_breaker", "add_circuit_breaker", {}),
    ("adapt.extend.infrastructure.add_outbox_pattern", "add_outbox_pattern", {}),
    ("adapt.extend.infrastructure.add_saga", "add_saga", {}),
    ("adapt.extend.infrastructure.add_arq_worker", "add_arq_worker", {}),
    ("adapt.extend.infrastructure.add_stripe_checkout", "add_stripe_checkout", {}),
    ("adapt.extend.infrastructure.add_email_templates", "add_email_templates", {}),
    ("adapt.extend.infrastructure.add_rate_limiting", "add_rate_limiting", {}),
    ("adapt.extend.infrastructure.add_scheduled_tasks", "add_scheduled_tasks", {}),
    ("adapt.extend.infrastructure.add_sqladmin", "add_sqladmin", {}),
    ("adapt.extend.infrastructure.add_celery_beat", "add_celery_beat", {}),
    ("adapt.extend.infrastructure.add_s3_storage", "add_s3_storage", {}),
    ("adapt.extend.infrastructure.add_health_deep", "add_health_deep", {}),
    ("adapt.extend.infrastructure.add_notifications", "add_notifications", {}),
    ("adapt.extend.infrastructure.add_stripe_subscription", "add_stripe_subscription", {}),
    ("adapt.extend.infrastructure.add_stripe_refund_flow", "add_stripe_refund_flow", {}),
    ("adapt.extend.infrastructure.add_temporal_workflow", "add_temporal_workflow", {}),
    ("adapt.extend.infrastructure.add_ml_model_server", "add_ml_model_server", {}),
    ("adapt.extend.infrastructure.add_ml_gpu_inference", "add_ml_gpu_inference", {}),
    ("adapt.extend.infrastructure.add_ml_model_registry", "add_ml_model_registry", {}),
    ("adapt.extend.auth_access.add_feature_toggles_api", "add_feature_toggles_api", {}),
    ("adapt.extend.auth_access.add_cedar_policies", "add_cedar_policies", {}),
    ("adapt.extend.auth_access.add_opa_integration", "add_opa_integration", {}),
    ("adapt.extend.realtime.add_websocket_presence", "add_websocket_presence", {}),
    # extend / realtime
    ("adapt.extend.realtime.add_sse", "add_sse", {}),
    ("adapt.extend.realtime.add_webhook_receiver", "add_webhook_receiver", {}),
    ("adapt.extend.realtime.add_webhook_sender", "add_webhook_sender", {}),
    ("adapt.extend.realtime.add_websocket_chat", "add_websocket_chat", {}),
    ("adapt.extend.api_design.add_graphql_subscriptions", "add_graphql_subscriptions", {}),
    # NEW TOOLS (Batch 4)
    ("adapt.extend.infrastructure.add_cors_config", "add_cors_config", {}),
    ("adapt.extend.api_design.add_cqrs", "add_cqrs", {}),
    ("adapt.extend.infrastructure.add_csrf_protection", "add_csrf_protection", {}),
    ("adapt.extend.crud_data.add_data_import", "add_data_import", {}),
    ("adapt.extend.crud_data.add_data_versioning", "add_data_versioning", {}),
    ("adapt.extend.testing_tools.add_database_migrations_ci", "add_database_migrations_ci", {}),
    ("adapt.extend.infrastructure.add_docker_production", "add_docker_production", {}),
    ("adapt.extend.testing_tools.add_e2e_test_suite", "add_e2e_test_suite", {}),
    ("adapt.extend.crud_data.add_event_sourcing", "add_event_sourcing", {}),
    ("adapt.extend.infrastructure.add_excel_export", "add_excel_export", {}),
    ("adapt.extend.infrastructure.add_input_sanitization", "add_input_sanitization", {}),
    ("adapt.extend.infrastructure.add_kubernetes_manifests", "add_kubernetes_manifests", {}),
    ("adapt.extend.infrastructure.add_opentelemetry", "add_opentelemetry", {}),
    ("adapt.extend.auth_access.add_passkey_auth", "add_passkey_auth", {}),
    ("adapt.extend.infrastructure.add_pdf_reports", "add_pdf_reports", {}),
    ("adapt.extend.infrastructure.add_prometheus_metrics", "add_prometheus_metrics", {}),
    (
        "adapt.extend.infrastructure.add_push_notifications_native",
        "add_push_notifications_native",
        {},
    ),
    ("adapt.extend.auth_access.add_sms_otp", "add_sms_otp", {}),
    ("adapt.extend.auth_access.add_social_login", "add_social_login", {}),
    ("adapt.extend.infrastructure.add_structured_logging", "add_structured_logging", {}),
    ("adapt.extend.infrastructure.add_transactional_email", "add_transactional_email", {}),
    # extend / testing_tools
    ("adapt.extend.testing_tools.add_contract_tests", "add_contract_tests", {}),
    ("adapt.extend.testing_tools.add_factory", "add_factory", {}),
    ("adapt.extend.testing_tools.add_load_profile", "add_load_profile", {}),
    # evolve (all have extra keyword params with sensible defaults)
    ("adapt.evolve.add_event_driven", "add_event_driven", {}),
    ("adapt.evolve.add_i18n", "add_i18n", {}),
    ("adapt.evolve.add_migration_data", "add_migration_data", {}),
    ("adapt.evolve.extract_service", "extract_service", {}),
    ("adapt.evolve.generate_admin_panel", "generate_admin_panel", {}),
    ("adapt.evolve.generate_docs", "generate_docs", {}),
    ("adapt.evolve.generate_sdk", "generate_sdk", {}),
    # refactor_model needs a non-empty target to do real work, but
    # calling with defaults (empty target) must still not crash
    ("adapt.evolve.refactor_model", "refactor_model", {}),
    # operate
    ("adapt.operate.api_changelog", "api_changelog", {}),
    ("adapt.operate.blast_radius", "blast_radius", {}),
    ("adapt.operate.connection_pool_monitor", "connection_pool_monitor", {}),
    ("adapt.operate.dead_code_finder", "dead_code_finder", {}),
    ("adapt.operate.dependency_graph", "dependency_graph", {}),
    ("adapt.operate.error_rate_analyzer", "error_rate_analyzer", {}),
    ("adapt.operate.migration_diff", "migration_diff", {}),
    ("adapt.operate.sla_reporter", "sla_reporter", {}),
    # verify
    ("adapt.verify.api_spec_compliance", "api_spec_compliance", {}),
    ("adapt.verify.dependency_audit", "dependency_audit", {}),
    ("adapt.verify.detect_n_plus_one", "detect_n_plus_one", {}),
    ("adapt.verify.performance_baseline", "performance_baseline", {}),
    ("adapt.verify.schema_coverage", "schema_coverage", {}),
    ("adapt.verify.security_scan", "security_scan", {}),
    # proactive
    ("adapt.proactive.fastapi_doctor", "fastapi_doctor", {}),
    # BATCH 5 (30 tools)
    (
        "adapt.extend.infrastructure.add_adaptive_throttle",
        "add_adaptive_throttle",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_adaptive_timeouts",
        "add_adaptive_timeouts",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_anomaly_detector",
        "add_anomaly_detector",
        {},
    ),
    (
        "adapt.extend.api_design.add_api_deprecation",
        "add_api_deprecation",
        {},
    ),
    (
        "adapt.extend.testing_tools.add_api_fuzzer",
        "add_api_fuzzer",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_api_monetization",
        "add_api_monetization",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_api_replay_debugger",
        "add_api_replay_debugger",
        {},
    ),
    (
        "adapt.extend.auth_access.add_bola_guard",
        "add_bola_guard",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_bulkhead_isolation",
        "add_bulkhead_isolation",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_canary_tokens",
        "add_canary_tokens",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_chaos_testing",
        "add_chaos_testing",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_compliance_engine",
        "add_compliance_engine",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_cost_tracker",
        "add_cost_tracker",
        {},
    ),
    (
        "adapt.extend.testing_tools.add_data_seeder",
        "add_data_seeder",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_dependency_health_map",
        "add_dependency_health_map",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_dlp_shield",
        "add_dlp_shield",
        {},
    ),
    (
        "adapt.extend.auth_access.add_dpop_tokens",
        "add_dpop_tokens",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_graceful_shutdown",
        "add_graceful_shutdown",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_load_shedding",
        "add_load_shedding",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_request_fingerprint",
        "add_request_fingerprint",
        {},
    ),
    (
        "adapt.extend.auth_access.add_request_signing",
        "add_request_signing",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_request_tracing_ui",
        "add_request_tracing_ui",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_response_armor",
        "add_response_armor",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_retry_budget",
        "add_retry_budget",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_runtime_sentinel",
        "add_runtime_sentinel",
        {},
    ),
    (
        "adapt.extend.testing_tools.add_sbom_guardian",
        "add_sbom_guardian",
        {},
    ),
    (
        "adapt.extend.testing_tools.add_schema_enforcer",
        "add_schema_enforcer",
        {},
    ),
    (
        "adapt.extend.testing_tools.add_schema_evolution_guard",
        "add_schema_evolution_guard",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_secret_rotation",
        "add_secret_rotation",
        {},
    ),
    (
        "adapt.extend.infrastructure.add_tenant_onboarding",
        "add_tenant_onboarding",
        {},
    ),
]


def _load_tool(module_path: str, fn_name: str) -> Callable:
    """Import module and return the named callable.

    Args:
        module_path: Dotted Python module path.
        fn_name: Name of the public function.

    Returns:
        The callable.

    Raises:
        ImportError: If the module or function cannot be loaded.
    """
    mod = importlib.import_module(module_path)
    return getattr(mod, fn_name)


def _call_tool(fn: Callable, inp: ToolInput, extra_kwargs: dict) -> ToolResult:
    """Call *fn* with *inp* and *extra_kwargs*.

    Args:
        fn: The adapt tool callable.
        inp: ToolInput to pass as the first positional argument.
        extra_kwargs: Additional keyword arguments forwarded to the tool.

    Returns:
        The ToolResult returned by the tool.
    """
    return fn(inp, **extra_kwargs)


def _dir_sha256(directory: Path) -> str:
    """Compute a deterministic SHA-256 fingerprint of all files under *directory*.

    Walks the directory tree, sorts paths for determinism, and hashes
    each file's relative path and content.

    Args:
        directory: Root directory to hash.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    hasher = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(directory))
            hasher.update(rel.encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _all_py_parse(directory: Path) -> list[str]:
    """Return a list of error messages for .py files that fail ast.parse.

    Args:
        directory: Root directory to check recursively.

    Returns:
        List of error strings; empty if all files parse correctly.
    """
    errors: list[str] = []
    for py_file in sorted(directory.rglob("*.py")):
        src = py_file.read_text(encoding="utf-8", errors="replace")
        try:
            ast.parse(src)
        except SyntaxError as exc:
            errors.append(f"{py_file}: {exc}")
    return errors


# ---------------------------------------------------------------------------
# Results accumulator
# ---------------------------------------------------------------------------


class _Results:
    """Simple pass/fail accumulator for a single property run."""

    def __init__(self, property_name: str, total_tools: int) -> None:
        self.property_name = property_name
        self.total_tools = total_tools
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def record(self, tool_label: str, ok: bool, reason: str = "") -> None:
        """Record one test result.

        Args:
            tool_label: Human-readable label (module.fn).
            ok: Whether the property held.
            reason: Failure description when *ok* is False.
        """
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(f"  [{tool_label}] {reason}")

    def summary(self) -> str:
        """Return a one-line summary string.

        Returns:
            Formatted summary with pass/fail counts and any failure details.
        """
        status = "PASS" if self.failed == 0 else "FAIL"
        lines = [f"  {status} {self.property_name}: {self.passed}/{self.total_tools} tools"]
        lines.extend(self.failures[:5])  # cap noise
        if len(self.failures) > 5:
            lines.append(f"  ... and {len(self.failures) - 5} more failures")
        return "\n".join(lines)

    @property
    def ok(self) -> bool:
        """True when all tools passed this property."""
        return self.failed == 0


def _load_all_tools() -> list[tuple[str, str, dict]]:
    """Return the full tool registry.

    Returns:
        List of (module_path, fn_name, extra_kwargs) tuples.
    """
    return list(_TOOL_REGISTRY)
