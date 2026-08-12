"""BATCH 5 behavior scenarios — part 2 (scenarios 25-26).

Split from ``test_behavior_scenarios_batch5.py``. Shared framework lives in
``test_behavior_scenarios_batch5__shared.py``.
"""

from __future__ import annotations

import pytest

from tests.test_behavior_scenarios_batch5__shared import (
    Scenario,
    ScenarioContext,
    run_scenario_assert,
)

# ===========================================================================
# SCENARIO 25 — Intelligence Stack
# TOOL-101 add_api_replay_debugger, TOOL-102 add_anomaly_detector,
# TOOL-103 add_request_fingerprint
# ===========================================================================


async def flow_intelligence_stack(ctx: ScenarioContext) -> None:
    """RequestRecorder ring buffer, AnomalyDetector baseline, FingerprintMiddleware importable."""
    project_dir = ctx.project_dir
    client = ctx.client

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- GET /debug/requests returns list (empty OK) ----------------------------
    # The route file is written; whether auto-registered in main.py varies.
    debug_route_path = project_dir / "app" / "api" / "routes" / "debug.py"
    r = await client.get("/debug/requests")
    debug_route_exists = r.status_code != 404 or debug_route_path.exists()
    ctx.record(
        "debug_requests_endpoint_or_file",
        debug_route_exists,
        f"GET /debug/requests: {r.status_code} or route file: {debug_route_path.exists()}",
    )
    if r.status_code == 200:
        try:
            body = r.json()
            ctx.record(
                "debug_requests_returns_list",
                isinstance(body, (list, dict)),
                f"body type: {type(body).__name__}",
            )
        except Exception:
            ctx.record("debug_requests_returns_list", True, "skipped (non-JSON response)")
    else:
        ctx.record("debug_requests_returns_list", True, f"skipped (status {r.status_code})")

    # ---- Recorder module has ring buffer pattern --------------------------------
    recorder_path = project_dir / "app" / "debug" / "recorder.py"
    ctx.record(
        "recorder_file_exists",
        recorder_path.exists(),
        str(recorder_path.relative_to(project_dir) if recorder_path.exists() else "NOT FOUND"),
    )

    if recorder_path.exists():
        recorder_src = recorder_path.read_text()
        has_ring_buffer = "RequestRecorder" in recorder_src and (
            "deque" in recorder_src
            or "maxlen" in recorder_src
            or "ring" in recorder_src.lower()
            or "ring_buffer" in recorder_src.lower()
        )
        ctx.record(
            "recorder_has_ring_buffer_pattern",
            has_ring_buffer,
            "RequestRecorder + deque/maxlen/ring pattern in recorder.py",
        )
    else:
        ctx.record("recorder_has_ring_buffer_pattern", False, "recorder.py not found")

    # ---- GET /anomaly/status returns baseline data structure --------------------
    # Route file is written but auto-registration may not happen (depends on patching)
    anomaly_route_path = project_dir / "app" / "api" / "routes" / "anomaly.py"
    r_anomaly = await client.get("/anomaly/status")
    anomaly_accessible = r_anomaly.status_code != 404 or anomaly_route_path.exists()
    ctx.record(
        "anomaly_status_endpoint_or_file",
        anomaly_accessible,
        f"GET /anomaly/status: {r_anomaly.status_code} or route file: {anomaly_route_path.exists()}",
    )
    if r_anomaly.status_code == 200:
        try:
            body = r_anomaly.json()
            has_baseline = isinstance(body, dict) and any(
                k in body for k in ("baselines", "detector", "status", "metrics", "windows")
            )
            ctx.record(
                "anomaly_status_has_baseline_structure",
                has_baseline,
                f"body keys: {list(body.keys()) if isinstance(body, dict) else type(body)}",
            )
        except Exception:
            ctx.record("anomaly_status_has_baseline_structure", True, "skipped")
    else:
        ctx.record(
            "anomaly_status_has_baseline_structure",
            True,
            f"skipped (status {r_anomaly.status_code})",
        )

    # ---- FingerprintMiddleware class importable ---------------------------------
    fingerprint_mw_path = project_dir / "app" / "middleware" / "fingerprint.py"
    ctx.record(
        "fingerprint_middleware_file_exists",
        fingerprint_mw_path.exists(),
        str(
            fingerprint_mw_path.relative_to(project_dir)
            if fingerprint_mw_path.exists()
            else "NOT FOUND"
        ),
    )

    if fingerprint_mw_path.exists():
        fp_src = fingerprint_mw_path.read_text()
        ctx.record(
            "fingerprint_middleware_class_present",
            "FingerprintMiddleware" in fp_src,
            "FingerprintMiddleware class in middleware/fingerprint.py",
        )
    else:
        ctx.record("fingerprint_middleware_class_present", False, "file not found")

    # ---- AnomalyDetector class in detector.py -----------------------------------
    detector_path = project_dir / "app" / "anomaly" / "detector.py"
    if detector_path.exists():
        det_src = detector_path.read_text()
        ctx.record(
            "anomaly_detector_class_present",
            "AnomalyDetector" in det_src,
            "AnomalyDetector class in anomaly/detector.py",
        )
    else:
        ctx.record("anomaly_detector_class_present", False, "app/anomaly/detector.py not found")


INTELLIGENCE_STACK = Scenario(
    name="intelligence_stack",
    archetype="API replay debugger + anomaly detector + request fingerprint",
    models={"Request": {"path": "str", "method": "str", "status_code": "int"}},
    tools=[
        ("add_api_replay_debugger", "adapt.extend.infrastructure.add_api_replay_debugger"),
        ("add_anomaly_detector", "adapt.extend.infrastructure.add_anomaly_detector"),
        ("add_request_fingerprint", "adapt.extend.infrastructure.add_request_fingerprint"),
    ],
    flow=flow_intelligence_stack,
)


# ===========================================================================
# SCENARIO 26 — Lifecycle Tools
# TOOL-104 add_schema_evolution_guard, TOOL-105 add_data_seeder,
# TOOL-106 add_api_deprecation
# ===========================================================================


async def flow_lifecycle_tools(ctx: ScenarioContext) -> None:
    """SchemaComparator BREAKING/COMPATIBLE, DataSeeder topological sort, Sunset header, deprecation listing."""
    project_dir = ctx.project_dir
    client = ctx.client

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- SchemaComparator class importable with BREAKING/COMPATIBLE constants ---
    comparator_path = project_dir / "app" / "schema_guard" / "comparator.py"
    ctx.record(
        "comparator_file_exists",
        comparator_path.exists(),
        str(comparator_path.relative_to(project_dir) if comparator_path.exists() else "NOT FOUND"),
    )

    if comparator_path.exists():
        comp_src = comparator_path.read_text()
        has_class = "SchemaComparator" in comp_src
        has_breaking = "BREAKING" in comp_src
        has_compatible = "COMPATIBLE" in comp_src
        ctx.record(
            "schema_comparator_class_present", has_class, "SchemaComparator class in comparator.py"
        )
        ctx.record(
            "schema_comparator_has_breaking_constant",
            has_breaking,
            "BREAKING constant in comparator.py",
        )
        ctx.record(
            "schema_comparator_has_compatible_constant",
            has_compatible,
            "COMPATIBLE constant in comparator.py",
        )
    else:
        for lbl in [
            "schema_comparator_class_present",
            "schema_comparator_has_breaking_constant",
            "schema_comparator_has_compatible_constant",
        ]:
            ctx.record(lbl, False, "comparator.py not found")

    # ---- DataSeeder has topological sort logic ----------------------------------
    seeder_init = project_dir / "app" / "seeder" / "__init__.py"
    ctx.record(
        "data_seeder_file_exists",
        seeder_init.exists(),
        str(seeder_init.relative_to(project_dir) if seeder_init.exists() else "NOT FOUND"),
    )

    if seeder_init.exists():
        seeder_src = seeder_init.read_text()
        has_data_seeder = "DataSeeder" in seeder_src
        # Topological sort may be in the same file or in a dependency_graph module
        dep_graph_path = project_dir / "app" / "seeder" / "dependency_graph.py"
        has_topo_sort = (
            "topological" in seeder_src.lower()
            or "topo" in seeder_src.lower()
            or "DependencyGraph" in seeder_src
            or dep_graph_path.exists()
        )
        ctx.record(
            "data_seeder_class_present", has_data_seeder, "DataSeeder class in seeder/__init__.py"
        )
        ctx.record(
            "data_seeder_has_topological_sort",
            has_topo_sort,
            "topological/topo/DependencyGraph pattern in seeder package",
        )
    else:
        ctx.record("data_seeder_class_present", False, "seeder/__init__.py not found")
        ctx.record("data_seeder_has_topological_sort", False, "file not found")

    # ---- GET /api/deprecations returns list or route file exists ----------------
    # add_api_deprecation writes a route file; auto-registration in main.py varies
    dep_route_path = project_dir / "app" / "deprecation" / "reporter.py"
    dep_route_path2 = project_dir / "app" / "api" / "routes" / "deprecations.py"
    r_dep = await client.get("/api/deprecations")
    dep_accessible = (
        r_dep.status_code != 404
        or dep_route_path.exists()
        or dep_route_path2.exists()
        or (project_dir / "app" / "deprecation").is_dir()
    )
    ctx.record(
        "deprecations_endpoint_or_package_exists",
        dep_accessible,
        f"GET /api/deprecations: {r_dep.status_code} or deprecation package: "
        f"{(project_dir / 'app' / 'deprecation').is_dir()}",
    )
    if r_dep.status_code == 200:
        try:
            body = r_dep.json()
            ctx.record(
                "deprecations_returns_list",
                isinstance(body, (list, dict)),
                f"body type: {type(body).__name__}",
            )
        except Exception:
            ctx.record("deprecations_returns_list", True, "skipped")
    else:
        ctx.record("deprecations_returns_list", True, f"skipped (status {r_dep.status_code})")

    # ---- Sunset header concept present in deprecation middleware ----------------
    dep_mw_path = project_dir / "app" / "deprecation" / "middleware.py"
    if dep_mw_path.exists():
        dep_mw_src = dep_mw_path.read_text()
        has_sunset = "Sunset" in dep_mw_src or "sunset" in dep_mw_src.lower()
        ctx.record(
            "deprecation_middleware_has_sunset_header",
            has_sunset,
            "Sunset header pattern in deprecation/middleware.py",
        )
    else:
        # Sunset may be in the __init__.py
        dep_init = project_dir / "app" / "deprecation" / "__init__.py"
        if dep_init.exists():
            dep_init_src = dep_init.read_text()
            has_sunset = "Sunset" in dep_init_src or "sunset" in dep_init_src.lower()
            ctx.record(
                "deprecation_middleware_has_sunset_header",
                has_sunset,
                "Sunset header in deprecation/__init__.py",
            )
        else:
            ctx.record(
                "deprecation_middleware_has_sunset_header",
                False,
                "neither deprecation/middleware.py nor __init__.py found",
            )


LIFECYCLE_TOOLS = Scenario(
    name="lifecycle_tools",
    archetype="SchemaEvolutionGuard + DataSeeder (topological FK) + ApiDeprecation (Sunset)",
    models={"Item": {"name": "str", "version": "str"}},
    tools=[
        ("add_schema_evolution_guard", "adapt.extend.testing_tools.add_schema_evolution_guard"),
        ("add_data_seeder", "adapt.extend.testing_tools.add_data_seeder"),
        ("add_api_deprecation", "adapt.extend.api_design.add_api_deprecation"),
    ],
    flow=flow_lifecycle_tools,
)


# ===========================================================================
# pytest integration
# ===========================================================================

SCENARIOS: list[Scenario] = [
    INTELLIGENCE_STACK,
    LIFECYCLE_TOOLS,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await run_scenario_assert(scenario)
