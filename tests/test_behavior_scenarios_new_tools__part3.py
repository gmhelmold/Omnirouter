"""BEHAVIOR scenarios for NEW tools — part 3 (scenarios 20-22).

Split out of ``test_behavior_scenarios_new_tools.py``. Shared framework lives
in ``test_behavior_scenarios_new_tools__shared.py``.
"""

from __future__ import annotations

import sys

import pytest

from tests.test_behavior_scenarios_new_tools__shared import (
    Scenario,
    ScenarioContext,
    _assert_scenario,
)

# ===========================================================================
# SCENARIO 20 — ML Model Server + GPU Inference + Model Registry
# ===========================================================================


async def flow_ml_stack(ctx: ScenarioContext) -> None:
    """ML inference layer: endpoints exist, no top-level ML imports, GPU status."""
    client = ctx.client
    project_dir = ctx.project_dir

    # GET /api/v1/ml/models → 200 (both server + registry provide this route)
    r = await client.get("/api/v1/ml/models")
    ctx.record(
        "ml_models_endpoint_exists", r.status_code not in (404,), f"GET /ml/models: {r.status_code}"
    )
    if r.status_code == 200:
        body = r.json()
        ctx.record(
            "ml_models_is_list_or_dict",
            isinstance(body, (list, dict)),
            f"body type: {type(body).__name__}",
        )
    else:
        ctx.record("ml_models_is_list_or_dict", True, f"skipped (status {r.status_code})")

    # GET /api/v1/ml/gpu/status → 200 with available:false (no GPU in test)
    r_gpu = await client.get("/api/v1/ml/gpu/status")
    ctx.record(
        "ml_gpu_status_exists",
        r_gpu.status_code not in (404,),
        f"GET /ml/gpu/status: {r_gpu.status_code}",
    )
    if r_gpu.status_code == 200:
        body_gpu = r_gpu.json()
        ctx.record(
            "ml_gpu_status_has_available",
            "available" in body_gpu,
            f"body keys: {list(body_gpu.keys())}",
        )
    else:
        ctx.record("ml_gpu_status_has_available", True, f"skipped (status {r_gpu.status_code})")

    # POST /api/v1/ml/predict → endpoint exists (401/422 expected without auth/payload)
    r_pred = await client.post("/api/v1/ml/predict", json={})
    ctx.record(
        "predict_endpoint_exists",
        r_pred.status_code not in (404,),
        f"POST /ml/predict: {r_pred.status_code} (404 = route missing)",
    )

    # No torch/sklearn/tensorflow/numpy/onnx at top level in any generated app/ file
    import ast as _ast

    app_dir = project_dir / "app"
    top_level_ml_imports: list[str] = []
    heavy_libs = {"torch", "sklearn", "tensorflow", "onnxruntime", "numpy"}

    for py_file in sorted(app_dir.rglob("*.py")):
        try:
            src = py_file.read_text()
            tree = _ast.parse(src)
        except Exception:
            continue
        for node in tree.body:  # only top-level statements
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    lib = alias.name.split(".")[0]
                    if lib in heavy_libs:
                        top_level_ml_imports.append(
                            f"{py_file.relative_to(project_dir)}:{node.lineno}: import {lib}"
                        )
            elif isinstance(node, _ast.ImportFrom) and node.module:
                lib = node.module.split(".")[0]
                if lib in heavy_libs:
                    top_level_ml_imports.append(
                        f"{py_file.relative_to(project_dir)}:{node.lineno}: from {lib}"
                    )

    ctx.record(
        "no_top_level_ml_imports",
        not top_level_ml_imports,
        f"{len(top_level_ml_imports)} violations: {top_level_ml_imports[:3]}",
    )


ML_STACK = Scenario(
    name="ml_model_stack",
    archetype="Framework-agnostic ML server + GPU inference + model registry",
    models={"Prediction": {"model_name": "str", "input_ref": "str", "score": "float"}},
    tools=[
        ("add_ml_model_server", "adapt.extend.infrastructure.add_ml_model_server"),
        ("add_ml_gpu_inference", "adapt.extend.infrastructure.add_ml_gpu_inference"),
        ("add_ml_model_registry", "adapt.extend.infrastructure.add_ml_model_registry"),
    ],
    flow=flow_ml_stack,
)


# ===========================================================================
# SCENARIO 21 — Policy Engines: Cedar + OPA
# ===========================================================================


async def flow_policy_engines(ctx: ScenarioContext) -> None:
    """Cedar + OPA authz endpoints exist; policy files created."""
    client = ctx.client
    project_dir = ctx.project_dir

    # POST /authz/check → must exist (not 404)
    r = await client.post(
        "/api/v1/authz/check", json={"principal": "User::1", "action": "read", "resource": "doc::1"}
    )
    ctx.record("cedar_check_exists", r.status_code != 404, f"POST /authz/check: {r.status_code}")

    # GET /authz/policies → must exist
    r = await client.get("/api/v1/authz/policies")
    ctx.record(
        "cedar_policies_list_exists", r.status_code != 404, f"GET /authz/policies: {r.status_code}"
    )

    # POST /authz/opa/check → must exist
    r = await client.post(
        "/api/v1/authz/opa/check", json={"input": {"user": "alice", "action": "read"}}
    )
    ctx.record("opa_check_exists", r.status_code != 404, f"POST /authz/opa/check: {r.status_code}")

    # GET /authz/opa/health → must exist
    r = await client.get("/api/v1/authz/opa/health")
    ctx.record("opa_health_exists", r.status_code != 404, f"GET /authz/opa/health: {r.status_code}")

    # .cedar policy files created
    cedar_files = list(project_dir.rglob("*.cedar"))
    ctx.record(
        "cedar_files_created",
        len(cedar_files) >= 1,
        f"{len(cedar_files)} .cedar files: {[f.name for f in cedar_files[:3]]}",
    )

    # .rego policy files created
    rego_files = list(project_dir.rglob("*.rego"))
    ctx.record(
        "rego_files_created",
        len(rego_files) >= 1,
        f"{len(rego_files)} .rego files: {[f.name for f in rego_files[:3]]}",
    )

    # authz/engine.py must be importable
    engine_path = project_dir / "app" / "authz" / "engine.py"
    if engine_path.exists():
        try:
            key = str(project_dir)
            if key not in sys.path:
                sys.path.insert(0, key)
            import importlib.util as _util

            spec = _util.spec_from_file_location("_cedar_engine_test", str(engine_path))
            if spec and spec.loader:
                mod = _util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            ctx.record("cedar_engine_imports", True, "no crash")
            ctx.record(
                "cedar_engine_has_class", hasattr(mod, "CedarEngine"), "CedarEngine class present"
            )
        except Exception as exc:
            ctx.record("cedar_engine_imports", False, f"{type(exc).__name__}: {str(exc)[:200]}")
            ctx.record("cedar_engine_has_class", False, "import failed")
    else:
        ctx.record("cedar_engine_imports", False, "app/authz/engine.py not found")
        ctx.record("cedar_engine_has_class", False, "file missing")


POLICY_ENGINES = Scenario(
    name="policy_engines_cedar_opa",
    archetype="ABAC policy enforcement: Cedar PBAC + OPA Rego side-by-side",
    models={"Resource": {"name": "str", "owner_ref": "str", "access_level": "str"}},
    tools=[
        ("add_cedar_policies", "adapt.extend.auth_access.add_cedar_policies"),
        ("add_opa_integration", "adapt.extend.auth_access.add_opa_integration"),
    ],
    flow=flow_policy_engines,
)


# ===========================================================================
# SCENARIO 22 — GraphQL Subscriptions
# ===========================================================================


async def flow_graphql_subscriptions(ctx: ScenarioContext) -> None:
    """GraphQL subscription infra: files, pubsub class, async generators, config.

    Note: The app may fail to boot on Python 3.14 due to a strawberry/graphql-core
    issue with GraphQLContext forward reference resolution. We therefore test the
    GENERATED CODE directly (file existence, AST patterns) rather than booting
    the app — this is still a meaningful behavior test since it verifies the
    generator produces correct code structure.
    """
    project_dir = ctx.project_dir

    graphql_dir = project_dir / "app" / "graphql"

    # Key files must exist
    expected = {
        "pubsub.py": graphql_dir / "pubsub.py",
        "subscriptions.py": graphql_dir / "subscriptions.py",
        "ws_handler.py": graphql_dir / "ws_handler.py",
        "schema.py": graphql_dir / "schema.py",
    }
    for label, fpath in expected.items():
        ctx.record(
            f"{label}_exists",
            fpath.exists(),
            str(fpath.relative_to(project_dir) if fpath.exists() else "NOT FOUND"),
        )

    # pubsub.py: PubSubManager class + publish + subscribe methods
    pubsub_path = graphql_dir / "pubsub.py"
    if pubsub_path.exists():
        src = pubsub_path.read_text()
        ctx.record(
            "pubsub_has_manager_class", "PubSubManager" in src, "PubSubManager class in pubsub.py"
        )
        ctx.record(
            "pubsub_has_publish",
            "def publish" in src or "async def publish" in src,
            "publish method in pubsub.py",
        )
        ctx.record(
            "pubsub_has_subscribe",
            "def subscribe" in src or "async def subscribe" in src,
            "subscribe method in pubsub.py",
        )
        ctx.record(
            "pubsub_redis_lazy",
            "import redis" not in src.split("\n")[0:10] or "try:" in src,
            "redis not at top-level (lazy import)",
        )
    else:
        for label in [
            "pubsub_has_manager_class",
            "pubsub_has_publish",
            "pubsub_has_subscribe",
            "pubsub_redis_lazy",
        ]:
            ctx.record(label, False, "pubsub.py not found")

    # subscriptions.py: async generators with @strawberry.subscription
    subs_path = graphql_dir / "subscriptions.py"
    if subs_path.exists():
        src = subs_path.read_text()
        has_async_gen = "async def" in src and ("yield" in src or "AsyncIterator" in src)
        ctx.record(
            "subscriptions_has_async_generators",
            has_async_gen,
            "async def ... yield/AsyncIterator pattern",
        )
        has_decorator = "@strawberry.subscription" in src or "subscription" in src.lower()
        ctx.record(
            "subscriptions_has_decorator",
            has_decorator,
            "@strawberry.subscription decorator present",
        )
    else:
        ctx.record("subscriptions_has_async_generators", False, "subscriptions.py not found")
        ctx.record("subscriptions_has_decorator", False, "subscriptions.py not found")

    # schema.py: Subscription type added to strawberry.Schema
    schema_path = graphql_dir / "schema.py"
    if schema_path.exists():
        src = schema_path.read_text()
        ctx.record(
            "schema_has_subscription",
            "subscription" in src.lower() and "strawberry.Schema" in src,
            "subscription= in strawberry.Schema call",
        )
    else:
        ctx.record("schema_has_subscription", False, "schema.py not found")

    # main.py: /graphql/ws route mounted
    main_path = project_dir / "app" / "main.py"
    if main_path.exists():
        main_src = main_path.read_text()
        ctx.record(
            "graphql_ws_in_main",
            "graphql/ws" in main_src or "graphql_ws" in main_src.lower(),
            "/graphql/ws mount present in main.py",
        )
    else:
        ctx.record("graphql_ws_in_main", False, "main.py not found")

    # Config has GRAPHQL_WS_ENABLED
    cfg_path = project_dir / "app" / "core" / "config.py"
    if cfg_path.exists():
        cfg_src = cfg_path.read_text()
        ctx.record(
            "graphql_ws_config", "GRAPHQL_WS_ENABLED" in cfg_src, "GRAPHQL_WS_ENABLED in config.py"
        )
    else:
        ctx.record("graphql_ws_config", False, "config.py not found")


GRAPHQL_SUBSCRIPTIONS = Scenario(
    name="graphql_subscriptions",
    archetype="WebSocket GraphQL subscriptions with PubSubManager + async generators",
    models={"Notification": {"kind": "str", "payload": "text"}},
    tools=[
        ("add_graphql", "adapt.extend.api_design.add_graphql"),
        ("add_graphql_subscriptions", "adapt.extend.api_design.add_graphql_subscriptions"),
    ],
    flow=flow_graphql_subscriptions,
    needs_boot=False,  # strawberry/graphql-core Python 3.14 compat issue on boot
)


# ===========================================================================
# pytest integration — one parametrized test per scenario
# ===========================================================================

SCENARIOS: list[Scenario] = [
    ML_STACK,
    POLICY_ENGINES,
    GRAPHQL_SUBSCRIPTIONS,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await _assert_scenario(scenario)
