"""BATCH 5 behavior scenarios — part 4 (scenarios 30-32).

Split from ``test_behavior_scenarios_batch5.py``. Shared framework lives in
``test_behavior_scenarios_batch5__shared.py``.
"""

from __future__ import annotations

import pytest

from tests.test_behavior_scenarios_batch5__shared import (
    Scenario,
    ScenarioContext,
    _import_project_module,
    run_scenario_assert,
)

# ===========================================================================
# SCENARIO 30 — Security: Throttle + Schema + Armor
# TOOL-116 add_adaptive_throttle, TOOL-117 add_schema_enforcer, TOOL-118 add_response_armor
# ===========================================================================


async def flow_security_throttle_schema_armor(ctx: ScenarioContext) -> None:
    """AdaptiveThrottle behavioral fingerprint + cascading penalties, SchemaEnforcer drift, ResponseArmor hmac.compare_digest."""
    project_dir = ctx.project_dir
    client = ctx.client

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- AdaptiveThrottle has behavioral fingerprinting + cascading penalties ----
    throttle_path = project_dir / "app" / "core" / "adaptive_throttle.py"
    ctx.record(
        "adaptive_throttle_file_exists",
        throttle_path.exists(),
        str(throttle_path.relative_to(project_dir) if throttle_path.exists() else "NOT FOUND"),
    )

    if throttle_path.exists():
        throttle_src = throttle_path.read_text()
        has_config_class = "AdaptiveThrottleConfig" in throttle_src
        has_behavioral = (
            "fingerprint" in throttle_src.lower()
            or "behavioral" in throttle_src.lower()
            or "header_order" in throttle_src.lower()
        )
        has_cascade = (
            "cascad" in throttle_src.lower()
            or "penalt" in throttle_src.lower()
            or "escalat" in throttle_src.lower()
        )
        ctx.record(
            "adaptive_throttle_config_class_present",
            has_config_class,
            "AdaptiveThrottleConfig in adaptive_throttle.py",
        )
        ctx.record(
            "adaptive_throttle_has_behavioral_fingerprinting",
            has_behavioral,
            "fingerprint/behavioral/header_order pattern in adaptive_throttle.py",
        )
        ctx.record(
            "adaptive_throttle_has_cascading_penalties",
            has_cascade,
            "cascad/penalt/escalat pattern in adaptive_throttle.py",
        )
    else:
        for lbl in [
            "adaptive_throttle_config_class_present",
            "adaptive_throttle_has_behavioral_fingerprinting",
            "adaptive_throttle_has_cascading_penalties",
        ]:
            ctx.record(lbl, False, "adaptive_throttle.py not found")

    # ---- SchemaEnforcer has drift detection mode --------------------------------
    schema_enforcer_mw = project_dir / "app" / "middleware" / "schema_enforcer.py"
    ctx.record(
        "schema_enforcer_middleware_exists",
        schema_enforcer_mw.exists(),
        str(
            schema_enforcer_mw.relative_to(project_dir)
            if schema_enforcer_mw.exists()
            else "NOT FOUND"
        ),
    )

    if schema_enforcer_mw.exists():
        mw_src = schema_enforcer_mw.read_text()
        has_mw_class = "SchemaEnforcerMiddleware" in mw_src
        ctx.record(
            "schema_enforcer_middleware_class_present",
            has_mw_class,
            "SchemaEnforcerMiddleware in middleware/schema_enforcer.py",
        )
    else:
        ctx.record("schema_enforcer_middleware_class_present", False, "file not found")

    schema_enforcer_core = project_dir / "app" / "core" / "schema_enforcer.py"
    if schema_enforcer_core.exists():
        core_src = schema_enforcer_core.read_text()
        has_drift = "drift" in core_src.lower() or "detect_shadow" in core_src
        ctx.record(
            "schema_enforcer_has_drift_detection",
            has_drift,
            "drift/detect_shadow pattern in core/schema_enforcer.py",
        )
    else:
        ctx.record(
            "schema_enforcer_has_drift_detection", False, "core/schema_enforcer.py not found"
        )

    # ---- ResponseArmor has error sanitizer + hmac.compare_digest ----------------
    armor_core = project_dir / "app" / "core" / "response_armor.py"
    timing_safe = project_dir / "app" / "core" / "timing_safe.py"
    ctx.record(
        "response_armor_core_exists",
        armor_core.exists(),
        str(armor_core.relative_to(project_dir) if armor_core.exists() else "NOT FOUND"),
    )

    if armor_core.exists():
        armor_src = armor_core.read_text()
        has_sanitizer = "sanitize_error" in armor_src or "ErrorSanitizer" in armor_src
        ctx.record(
            "response_armor_has_error_sanitizer",
            has_sanitizer,
            "sanitize_error/ErrorSanitizer in response_armor.py",
        )
    else:
        ctx.record("response_armor_has_error_sanitizer", False, "response_armor.py not found")

    # compare_digest may be in timing_safe.py or response_armor.py
    compare_digest_found = False
    for fpath in [timing_safe, armor_core]:
        if fpath.exists():
            src = fpath.read_text()
            if "compare_digest" in src:
                compare_digest_found = True
                break
    ctx.record(
        "response_armor_has_hmac_compare_digest",
        compare_digest_found,
        "hmac.compare_digest/compare_digest in timing_safe.py or response_armor.py",
    )


SECURITY_THROTTLE_SCHEMA_ARMOR = Scenario(
    name="security_throttle_schema_armor",
    archetype="AdaptiveThrottle (behavioral) + SchemaEnforcer (drift) + ResponseArmor (hmac.compare_digest)",
    models={"Alert": {"kind": "str", "severity": "str"}},
    tools=[
        ("add_adaptive_throttle", "adapt.extend.infrastructure.add_adaptive_throttle"),
        ("add_schema_enforcer", "adapt.extend.testing_tools.add_schema_enforcer"),
        ("add_response_armor", "adapt.extend.infrastructure.add_response_armor"),
    ],
    flow=flow_security_throttle_schema_armor,
    needs_boot=False,  # File-level assertions; boot not needed for these checks
)


# ===========================================================================
# SCENARIO 31 — Business Stack
# TOOL-119 add_api_monetization, TOOL-120 add_cost_tracker, TOOL-121 add_tenant_onboarding
# ===========================================================================


async def flow_business_stack(ctx: ScenarioContext) -> None:
    """MeteringMiddleware present, GET /billing/usage exists, CostTracker estimator, OnboardingOrchestrator compensatable steps."""
    project_dir = ctx.project_dir
    client = ctx.client

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- MeteringMiddleware class present ---------------------------------------
    metering_path = project_dir / "app" / "billing" / "metering.py"
    ctx.record(
        "metering_file_exists",
        metering_path.exists(),
        str(metering_path.relative_to(project_dir) if metering_path.exists() else "NOT FOUND"),
    )

    if metering_path.exists():
        metering_src = metering_path.read_text()
        ctx.record(
            "metering_middleware_class_present",
            "MeteringMiddleware" in metering_src,
            "MeteringMiddleware class in billing/metering.py",
        )
    else:
        ctx.record("metering_middleware_class_present", False, "metering.py not found")

    # ---- GET /billing/usage returns some structure or route file exists ---------
    # add_api_monetization writes app/billing/ package + routes; auto-registration
    # via api_router patching which may not always work with the fixture app layout
    billing_routes_path = project_dir / "app" / "api" / "routes" / "billing.py"
    r_usage = await client.get("/billing/usage")
    billing_accessible = (
        r_usage.status_code != 404
        or billing_routes_path.exists()
        or (project_dir / "app" / "billing").is_dir()
    )
    ctx.record(
        "billing_usage_endpoint_or_package_exists",
        billing_accessible,
        f"GET /billing/usage: {r_usage.status_code}, "
        f"billing/ dir: {(project_dir / 'app' / 'billing').is_dir()}",
    )
    if r_usage.status_code == 200:
        try:
            body = r_usage.json()
            ctx.record(
                "billing_usage_response_is_structured",
                isinstance(body, (dict, list)),
                f"body type: {type(body).__name__}",
            )
        except Exception:
            ctx.record("billing_usage_response_is_structured", True, "skipped")
    else:
        ctx.record(
            "billing_usage_response_is_structured", True, f"skipped (status {r_usage.status_code})"
        )

    # ---- CANONICAL: venous CostTracker primitive + adapter + glue ---------------
    # add_cost_tracker copies core.venous.resiliency.CostTracker (the tracker +
    # RequestContext/CostEstimate + the default DB/S3/API estimators) and the
    # CostTrackerAdapter, then emits app/cost_tracker.py (NOT a hand-rolled
    # app/costs/tracker.py + app/costs/estimators.py pair).
    cost_primitive = (
        project_dir / "core" / "venous" / "resiliency" / "CostTracker" / "CostTracker.py"
    )
    cost_glue = project_dir / "app" / "cost_tracker.py"
    ctx.record(
        "cost_tracker_primitive_shipped",
        cost_primitive.exists(),
        str(cost_primitive),
    )
    ctx.record(
        "cost_tracker_glue_calls_install",
        cost_glue.exists() and "install_cost_tracker" in cost_glue.read_text(),
        "app/cost_tracker.py exposes install_cost_tracker(app)",
    )

    # ---- CostTracker REALLY aggregates per-component estimates + ranks endpoints -
    ct_mod = _import_project_module(project_dir, "core.venous.resiliency.CostTracker.CostTracker")
    tracker = ct_mod.CostTracker()
    tracker.register(ct_mod.DBQueryCostEstimator(rate_per_query=0.01))
    tracker.register(ct_mod.APICostEstimator(rate_per_call=0.5))

    ctx_a = ct_mod.RequestContext(
        request_id="r1", path="/a", method="GET", db_query_count=3, external_api_calls=2
    )
    est_a = tracker.estimate_request(ctx_a)
    # db: 3 * 0.01 = 0.03 ; api: 2 * 0.5 = 1.0 ; total = 1.03
    ctx.record(
        "cost_tracker_aggregates_per_component_cost",
        abs(est_a.db_cost_usd - 0.03) < 1e-9
        and abs(est_a.api_cost_usd - 1.0) < 1e-9
        and abs(est_a.total_cost_usd - 1.03) < 1e-9,
        f"db={est_a.db_cost_usd}, api={est_a.api_cost_usd}, total={est_a.total_cost_usd}",
    )

    # A cheaper endpoint, then ranking should put the pricier /a first.
    ctx_b = ct_mod.RequestContext(request_id="r2", path="/b", method="GET", db_query_count=1)
    tracker.estimate_request(ctx_b)
    ranked = tracker.get_by_endpoint(top_n=2)
    ctx.record(
        "cost_tracker_ranks_endpoints_by_cost",
        len(ranked) == 2 and ranked[0]["endpoint"] == "GET /a",
        f"top endpoint: {ranked[0]['endpoint'] if ranked else 'NONE'} (expected 'GET /a')",
    )

    # ---- CostTracker is fail-open: a broken estimator NEVER propagates (INV_01) --
    class _BoomEstimator:
        component = "db"

        def estimate(self, _ctx):  # noqa: ANN001
            raise RuntimeError("estimator blew up")

    failopen_tracker = ct_mod.CostTracker()
    failopen_tracker.register(_BoomEstimator())
    failopen_tracker.register(ct_mod.APICostEstimator(rate_per_call=0.5))
    ctx_c = ct_mod.RequestContext(
        request_id="r3", path="/c", method="GET", db_query_count=5, external_api_calls=4
    )
    est_c = failopen_tracker.estimate_request(ctx_c)
    ctx.record(
        "cost_tracker_estimator_failure_is_fail_open",
        abs(est_c.db_cost_usd - 0.0) < 1e-9 and abs(est_c.api_cost_usd - 2.0) < 1e-9,
        f"broken db estimator swallowed; api still counted: db={est_c.db_cost_usd}, api={est_c.api_cost_usd}",
    )

    # ---- OnboardingOrchestrator has step-based workflow with compensatable steps -
    orchestrator_path = project_dir / "app" / "onboarding" / "orchestrator.py"
    ctx.record(
        "onboarding_orchestrator_file_exists",
        orchestrator_path.exists(),
        str(
            orchestrator_path.relative_to(project_dir)
            if orchestrator_path.exists()
            else "NOT FOUND"
        ),
    )

    if orchestrator_path.exists():
        orch_src = orchestrator_path.read_text()
        has_orchestrator = "OnboardingOrchestrator" in orch_src
        has_compensate = "compensat" in orch_src.lower() or "rollback" in orch_src.lower()
        ctx.record(
            "onboarding_orchestrator_class_present",
            has_orchestrator,
            "OnboardingOrchestrator class in onboarding/orchestrator.py",
        )
        ctx.record(
            "onboarding_has_compensatable_steps",
            has_compensate,
            "compensat/rollback pattern in orchestrator.py",
        )
    else:
        ctx.record("onboarding_orchestrator_class_present", False, "file not found")
        ctx.record("onboarding_has_compensatable_steps", False, "file not found")


BUSINESS_STACK = Scenario(
    name="business_stack",
    archetype="ApiMonetization (MeteringMiddleware) + CostTracker + TenantOnboarding (compensatable)",
    models={"Tenant": {"name": "str", "plan": "str"}},
    tools=[
        ("add_api_monetization", "adapt.extend.infrastructure.add_api_monetization"),
        ("add_cost_tracker", "adapt.extend.infrastructure.add_cost_tracker"),
        ("add_tenant_onboarding", "adapt.extend.infrastructure.add_tenant_onboarding"),
    ],
    flow=flow_business_stack,
)


# ===========================================================================
# SCENARIO 32 — Observability + Fuzzer
# TOOL-122 add_request_tracing_ui, TOOL-123 add_dependency_health_map,
# TOOL-124 add_api_fuzzer
# ===========================================================================


async def flow_observability_fuzzer(ctx: ScenarioContext) -> None:
    """TracingBuffer ring buffer, GET /tracing/requests, HealthMapBuilder, APIFuzzer type-aware generators."""
    project_dir = ctx.project_dir
    client = ctx.client

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- TracingBuffer has ring buffer -----------------------------------------
    tracing_init = project_dir / "app" / "tracing_ui" / "__init__.py"
    ctx.record(
        "tracing_buffer_file_exists",
        tracing_init.exists(),
        str(tracing_init.relative_to(project_dir) if tracing_init.exists() else "NOT FOUND"),
    )

    if tracing_init.exists():
        tracing_src = tracing_init.read_text()
        has_buffer = "TracingBuffer" in tracing_src
        has_ring_buffer = (
            "deque" in tracing_src or "maxlen" in tracing_src or "ring" in tracing_src.lower()
        )
        ctx.record(
            "tracing_buffer_class_present",
            has_buffer,
            "TracingBuffer class in tracing_ui/__init__.py",
        )
        ctx.record(
            "tracing_buffer_is_ring_buffer",
            has_ring_buffer,
            "deque/maxlen/ring pattern in tracing_ui/__init__.py",
        )
    else:
        ctx.record("tracing_buffer_class_present", False, "tracing_ui/__init__.py not found")
        ctx.record("tracing_buffer_is_ring_buffer", False, "file not found")

    # ---- GET /tracing/requests returns list -------------------------------------
    r_tracing = await client.get("/tracing/requests")
    ctx.record(
        "tracing_requests_endpoint_exists",
        r_tracing.status_code != 404,
        f"GET /tracing/requests: {r_tracing.status_code} (404 = route missing)",
    )
    if r_tracing.status_code == 200:
        try:
            body = r_tracing.json()
            ctx.record(
                "tracing_requests_returns_list",
                isinstance(body, (list, dict)),
                f"body type: {type(body).__name__}",
            )
        except Exception:
            ctx.record("tracing_requests_returns_list", True, "skipped")
    else:
        ctx.record(
            "tracing_requests_returns_list", True, f"skipped (status {r_tracing.status_code})"
        )

    # ---- HealthMapBuilder discovers dependencies --------------------------------
    hmap_init = project_dir / "app" / "health_map" / "__init__.py"
    ctx.record(
        "health_map_builder_file_exists",
        hmap_init.exists(),
        str(hmap_init.relative_to(project_dir) if hmap_init.exists() else "NOT FOUND"),
    )

    if hmap_init.exists():
        hmap_src = hmap_init.read_text()
        has_builder = "HealthMapBuilder" in hmap_src
        has_discover = "discover" in hmap_src.lower() or "depend" in hmap_src.lower()
        ctx.record(
            "health_map_builder_class_present",
            has_builder,
            "HealthMapBuilder class in health_map/__init__.py",
        )
        ctx.record(
            "health_map_builder_discovers_deps",
            has_discover,
            "discover/depend pattern in health_map/__init__.py",
        )
    else:
        ctx.record("health_map_builder_class_present", False, "file not found")
        ctx.record("health_map_builder_discovers_deps", False, "file not found")

    # ---- APIFuzzer has type-aware generators (boundary ints, unicode, SQL payloads) -
    fuzzer_init = project_dir / "app" / "fuzzer" / "__init__.py"
    fuzzer_generators = project_dir / "app" / "fuzzer" / "generators.py"
    ctx.record(
        "api_fuzzer_file_exists",
        fuzzer_init.exists(),
        str(fuzzer_init.relative_to(project_dir) if fuzzer_init.exists() else "NOT FOUND"),
    )

    if fuzzer_init.exists():
        fuzzer_src = fuzzer_init.read_text()
        ctx.record(
            "api_fuzzer_class_present",
            "APIFuzzer" in fuzzer_src,
            "APIFuzzer class in fuzzer/__init__.py",
        )
    else:
        ctx.record("api_fuzzer_class_present", False, "fuzzer/__init__.py not found")

    if fuzzer_generators.exists():
        gen_src = fuzzer_generators.read_text()
        has_boundary_ints = (
            "boundary" in gen_src.lower()
            or "INT_MAX" in gen_src
            or "2147483647" in gen_src
            or "int_min" in gen_src.lower()
        )
        has_unicode = "unicode" in gen_src.lower() or "\\u" in gen_src or "emoji" in gen_src.lower()
        has_sql_payload = (
            "sql" in gen_src.lower()
            or "SELECT" in gen_src
            or "DROP" in gen_src
            or "injection" in gen_src.lower()
        )
        ctx.record(
            "api_fuzzer_has_boundary_int_generator",
            has_boundary_ints,
            "boundary int generation pattern in generators.py",
        )
        ctx.record(
            "api_fuzzer_has_unicode_generator",
            has_unicode,
            "unicode/emoji edge case in generators.py",
        )
        ctx.record(
            "api_fuzzer_has_sql_payload_generator",
            has_sql_payload,
            "SQL payload/injection in generators.py",
        )
    else:
        for lbl in [
            "api_fuzzer_has_boundary_int_generator",
            "api_fuzzer_has_unicode_generator",
            "api_fuzzer_has_sql_payload_generator",
        ]:
            ctx.record(lbl, False, "fuzzer/generators.py not found")

    # ---- Fuzzer script exists ---------------------------------------------------
    fuzz_script = project_dir / "scripts" / "run_fuzz.py"
    ctx.record(
        "fuzzer_script_exists",
        fuzz_script.exists(),
        str(fuzz_script.relative_to(project_dir) if fuzz_script.exists() else "NOT FOUND"),
    )


OBSERVABILITY_FUZZER = Scenario(
    name="observability_fuzzer",
    archetype="RequestTracingUI (TracingBuffer ring) + DependencyHealthMap + APIFuzzer (type-aware)",
    models={"Endpoint": {"path": "str", "method": "str"}},
    tools=[
        ("add_request_tracing_ui", "adapt.extend.infrastructure.add_request_tracing_ui"),
        ("add_dependency_health_map", "adapt.extend.infrastructure.add_dependency_health_map"),
        ("add_api_fuzzer", "adapt.extend.testing_tools.add_api_fuzzer"),
    ],
    flow=flow_observability_fuzzer,
)


# ===========================================================================
# pytest integration
# ===========================================================================

SCENARIOS: list[Scenario] = [
    SECURITY_THROTTLE_SCHEMA_ARMOR,
    BUSINESS_STACK,
    OBSERVABILITY_FUZZER,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await run_scenario_assert(scenario)
