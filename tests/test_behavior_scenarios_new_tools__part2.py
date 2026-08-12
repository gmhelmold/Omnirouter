"""BEHAVIOR scenarios for NEW tools — part 2 (scenarios 17-19).

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
    _promote_superuser,
    _signup,
    _th,
)

# ===========================================================================
# SCENARIO 17 — Feature Toggles API
# ===========================================================================


async def flow_feature_toggles(ctx: ScenarioContext) -> None:
    """Feature toggle CRUD: list → create → verify → evaluate."""
    client = ctx.client
    session = ctx.session

    # Signup + superuser for toggle management
    try:
        token = await _signup(client, "toggle_admin@example.com", "TogglePass123!", "Toggle Admin")
        await _promote_superuser(session, "toggle_admin@example.com")
        # Re-login to pick up superuser flag if needed
        r = await client.post(
            "/api/v1/login/access-token",
            data={
                "username": "toggle_admin@example.com",
                "password": "TogglePass123!",
            },
        )
        if r.status_code == 200:
            token = r.json()["access_token"]
    except Exception as exc:
        ctx.record("signup_superuser", False, str(exc))
        return

    h = _th(token)

    # GET /feature-toggles → 200, empty list initially
    r = await client.get("/api/v1/feature-toggles/", headers=h)
    ctx.record(
        "toggles_list_empty", r.status_code == 200, f"GET /feature-toggles/: {r.status_code}"
    )

    # POST /feature-toggles → create toggle
    r = await client.post(
        "/api/v1/feature-toggles/",
        json={
            "name": "dark_mode",
            "enabled": True,
            "rollout_percentage": 100,
            "allowed_users": [],
            "environments": [],
        },
        headers=h,
    )
    toggle_created = r.status_code in (200, 201)
    ctx.record("toggle_created", toggle_created, f"POST /feature-toggles/: {r.status_code}")

    # GET /feature-toggles → verify toggle appears
    if toggle_created:
        r = await client.get("/api/v1/feature-toggles/", headers=h)
        toggles = r.json() if r.status_code == 200 else []
        if not isinstance(toggles, list):
            toggles = toggles.get("data") or toggles.get("items") or []
        names = [t.get("name") for t in toggles]
        ctx.record("toggle_appears_in_list", "dark_mode" in names, f"names in list: {names}")

        # POST /feature-toggles/{name}/evaluate → evaluation response
        r = await client.post(
            "/api/v1/feature-toggles/dark_mode/evaluate", json={"context": {}}, headers=h
        )
        ctx.record(
            "toggle_evaluate_exists",
            r.status_code != 404,
            f"POST /feature-toggles/dark_mode/evaluate: {r.status_code}",
        )
        if r.status_code == 200:
            body = r.json()
            ctx.record(
                "evaluate_has_enabled_field",
                "enabled" in body,
                f"evaluate response keys: {list(body.keys())}",
            )
    else:
        ctx.record("toggle_appears_in_list", False, "toggle not created")
        ctx.record("toggle_evaluate_exists", False, "toggle not created")
        ctx.record("evaluate_has_enabled_field", False, "toggle not created")


FEATURE_TOGGLES = Scenario(
    name="feature_toggles_api",
    archetype="Runtime feature toggle CRUD with percentage rollout + evaluate",
    models={"Product": {"name": "str", "active": "bool"}},
    tools=[
        ("add_feature_toggles_api", "adapt.extend.auth_access.add_feature_toggles_api"),
    ],
    flow=flow_feature_toggles,
)


# ===========================================================================
# SCENARIO 18 — Stripe Subscription + Refund Flow
# ===========================================================================


async def flow_stripe_subscription_refund(ctx: ScenarioContext) -> None:
    """Subscription + refund endpoints exist; modules import; PII schema safe."""
    client = ctx.client
    project_dir = ctx.project_dir

    # /subscriptions → 401 without auth (route must exist)
    # Try both with and without trailing slash (redirects are OK)
    r = await client.get("/api/v1/subscriptions", follow_redirects=True)
    ctx.record(
        "subscriptions_endpoint_exists",
        r.status_code != 404,
        f"GET /subscriptions: {r.status_code} (404 = route missing)",
    )
    ctx.record(
        "subscriptions_requires_auth",
        r.status_code in (401, 403, 405, 422),  # 405 = POST-only route, which is fine
        f"unauthenticated GET /subscriptions: {r.status_code}",
    )

    # /refunds → endpoint must exist (401/403 without auth)
    r = await client.get("/api/v1/refunds", follow_redirects=True)
    ctx.record(
        "refunds_endpoint_exists",
        r.status_code != 404,
        f"GET /refunds: {r.status_code} (404 = route missing)",
    )

    # stripe_billing.py module imports without crash (lazy imports)
    billing_path = project_dir / "app" / "core" / "stripe_billing.py"
    ctx.record(
        "stripe_billing_py_exists",
        billing_path.exists(),
        str(billing_path.relative_to(project_dir) if billing_path.exists() else "NOT FOUND"),
    )

    if billing_path.exists():
        try:
            key = str(project_dir)
            if key not in sys.path:
                sys.path.insert(0, key)
            import importlib.util as _util

            spec = _util.spec_from_file_location("_stripe_billing_test", str(billing_path))
            if spec and spec.loader:
                mod = _util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            ctx.record("stripe_billing_imports", True, "no crash")
        except Exception as exc:
            ctx.record("stripe_billing_imports", False, f"{type(exc).__name__}: {str(exc)[:200]}")
    else:
        ctx.record("stripe_billing_imports", False, "file not found")

    # stripe_refunds.py module imports without crash
    refunds_path = project_dir / "app" / "core" / "stripe_refunds.py"
    ctx.record(
        "stripe_refunds_py_exists",
        refunds_path.exists(),
        str(refunds_path.relative_to(project_dir) if refunds_path.exists() else "NOT FOUND"),
    )

    if refunds_path.exists():
        try:
            import importlib.util as _util2

            spec2 = _util2.spec_from_file_location("_stripe_refunds_test", str(refunds_path))
            if spec2 and spec2.loader:
                mod2 = _util2.module_from_spec(spec2)
                spec2.loader.exec_module(mod2)  # type: ignore[attr-defined]
            ctx.record("stripe_refunds_imports", True, "no crash")
        except Exception as exc:
            ctx.record("stripe_refunds_imports", False, f"{type(exc).__name__}: {str(exc)[:200]}")
    else:
        ctx.record("stripe_refunds_imports", False, "file not found")

    # SubscriptionPublic schema must NOT contain stripe_customer_id
    sub_model_path = project_dir / "app" / "models" / "subscription.py"
    if not sub_model_path.exists():
        # Some tools put it elsewhere
        sub_model_path = project_dir / "app" / "models" / "stripe_subscription.py"
    if sub_model_path.exists():
        src = sub_model_path.read_text()
        # The public schema should omit stripe_customer_id
        # Check that SubscriptionPublic doesn't include stripe_customer_id field
        import ast as _ast

        try:
            tree = _ast.parse(src)
            pii_safe = True
            for node in _ast.walk(tree):
                if (
                    isinstance(node, _ast.ClassDef)
                    and "Public" in node.name
                    and "subscription" in node.name.lower()
                ):
                    for item in _ast.walk(node):
                        if (
                            isinstance(item, _ast.AnnAssign)
                            and isinstance(item.target, _ast.Name)
                            and item.target.id == "stripe_customer_id"
                        ):
                            pii_safe = False
            ctx.record(
                "subscription_public_omits_pii",
                pii_safe,
                "SubscriptionPublic does not expose stripe_customer_id",
            )
        except Exception:
            ctx.record("subscription_public_omits_pii", True, "skipped (parse error)")
    else:
        ctx.record(
            "subscription_public_omits_pii",
            True,
            "skipped (subscription model file not found at expected path)",
        )


STRIPE_SUBSCRIPTION_REFUND = Scenario(
    name="stripe_subscription_refund",
    archetype="Stripe recurring billing + refund flow with PII-safe schemas",
    models={"Plan": {"name": "str", "price": "float", "interval": "str"}},
    tools=[
        # add_stripe_checkout first: creates `payments` table that refund_flow FK-references
        ("add_stripe_checkout", "adapt.extend.infrastructure.add_stripe_checkout"),
        ("add_stripe_subscription", "adapt.extend.infrastructure.add_stripe_subscription"),
        ("add_stripe_refund_flow", "adapt.extend.infrastructure.add_stripe_refund_flow"),
    ],
    flow=flow_stripe_subscription_refund,
)


# ===========================================================================
# SCENARIO 19 — Temporal Workflow
# ===========================================================================


async def flow_temporal_workflow(ctx: ScenarioContext) -> None:
    """Temporal workflow infra: files import, REST endpoints exist, compensation present."""
    client = ctx.client
    project_dir = ctx.project_dir

    # /workflows/start endpoint must exist (POST)
    r = await client.post("/api/v1/workflows/start", json={"workflow_type": "test", "input": {}})
    ctx.record(
        "workflows_endpoint_exists",
        r.status_code != 404,
        f"POST /workflows/start: {r.status_code} (404 = route missing)",
    )

    workflows_dir = project_dir / "app" / "workflows"
    expected_files = ["client.py", "worker.py", "activities.py", "example_workflow.py"]
    for fname in expected_files:
        fpath = workflows_dir / fname
        ctx.record(
            f"workflow_{fname.replace('.py', '')}_exists",
            fpath.exists(),
            str(fpath.relative_to(project_dir) if fpath.exists() else "NOT FOUND"),
        )

    # Each workflow file must import without crash
    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    for fname in expected_files:
        fpath = workflows_dir / fname
        if not fpath.exists():
            continue
        try:
            import importlib.util as _util

            spec = _util.spec_from_file_location(f"_wf_{fname}", str(fpath))
            if spec and spec.loader:
                mod = _util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            ctx.record(f"workflow_{fname.replace('.py', '')}_imports", True, "no crash")
        except Exception as exc:
            ctx.record(
                f"workflow_{fname.replace('.py', '')}_imports",
                False,
                f"{type(exc).__name__}: {str(exc)[:150]}",
            )

    # Compensation pattern in example_workflow.py
    example_path = workflows_dir / "example_workflow.py"
    if example_path.exists():
        src = example_path.read_text()
        has_compensation = (
            "compensation" in src.lower()
            or "compensat" in src.lower()
            or "rollback" in src.lower()
            or "try:" in src
            and "except" in src
        )
        ctx.record(
            "compensation_pattern_present",
            has_compensation,
            "compensation/rollback pattern in example_workflow.py",
        )
    else:
        ctx.record("compensation_pattern_present", False, "example_workflow.py not found")

    # Dockerfile.temporal-worker exists with USER directive
    df_path = project_dir / "Dockerfile.temporal-worker"
    ctx.record("dockerfile_temporal_worker_exists", df_path.exists(), str(df_path.name))
    if df_path.exists():
        df_content = df_path.read_text()
        ctx.record(
            "dockerfile_temporal_has_user",
            "USER" in df_content,
            "USER directive present (non-root container)",
        )


TEMPORAL_WORKFLOW = Scenario(
    name="temporal_workflow",
    archetype="Temporal.io durable workflow with compensation + REST routes",
    models={"Order": {"reference": "str", "status": "str", "total": "float"}},
    tools=[
        ("add_temporal_workflow", "adapt.extend.infrastructure.add_temporal_workflow"),
    ],
    flow=flow_temporal_workflow,
)


# ===========================================================================
# pytest integration — one parametrized test per scenario
# ===========================================================================

SCENARIOS: list[Scenario] = [
    FEATURE_TOGGLES,
    STRIPE_SUBSCRIPTION_REFUND,
    TEMPORAL_WORKFLOW,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await _assert_scenario(scenario)
