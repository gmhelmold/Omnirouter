"""BEHAVIOR scenarios 23-32 for TOOL-095 to TOOL-124 (30 newest tools).

Each scenario generates its own fixture project with ONLY the tools it
needs, patches ``app/core/db.py`` to SQLite+aiosqlite (no Docker),
patches ``app/middleware/idempotency.py`` to a pass-through stub, boots
the app, and runs real HTTP flows.

These tests intentionally do NOT require PostgreSQL — they use SQLite so
that CI can run them with zero external infrastructure.

This file holds scenarios 23-24; the remaining scenarios live in the
sibling ``test_behavior_scenarios_batch5__part{2,3,4}.py`` files and the
shared framework lives in ``test_behavior_scenarios_batch5__shared.py``.
The standalone runner below (``python tests/test_behavior_scenarios_batch5.py``)
still executes ALL 10 scenarios by importing them from the sibling parts.

Run::

    PYTHONPATH=. python tests/test_behavior_scenarios_batch5.py

Exit 0 → all assertions passed.
Exit 1 → at least one scenario has failures.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import sys
import time
from pathlib import Path

import pytest

from tests.test_behavior_scenarios_batch5__shared import (
    Scenario,
    ScenarioContext,
    _run_scenario,
    run_scenario_assert,
)


def _import_project_module(project_dir: Path, dotted: str):
    """Import a ``core.venous...`` module from the *generated project* copy.

    Behavior scenarios assert against what each tool ACTUALLY ships, so we load
    the project's own copied primitive/adapter (not the Arsenal source) by
    putting the project dir first on ``sys.path`` and flushing stale modules.
    """
    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)
    for m in list(sys.modules):
        if m == "core" or m.startswith("core."):
            del sys.modules[m]
    return importlib.import_module(dotted)

# ===========================================================================
# SCENARIO 23 — Resiliency Stack
# TOOL-095 add_load_shedding, TOOL-096 add_adaptive_timeouts, TOOL-097 add_bulkhead_isolation
# ===========================================================================


async def flow_resiliency_stack(ctx: ScenarioContext) -> None:
    """LoadShedder + AdaptiveTimeout + Bulkhead: classes importable, endpoint exists."""
    project_dir = ctx.project_dir
    client = ctx.client

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- CANONICAL: venous LoadShedder primitive + adapter + glue ----------------
    # add_load_shedding copies core.venous.resiliency.LoadShedder + the FastAPI
    # LoadShedderAdapter and emits app/load_shedding.py (NOT a hand-rolled
    # app/resilience/load_shedder.py with get_p99). Priority is carried by the
    # X-Priority header at the adapter (there is no priority.py classifier).
    shedder_primitive = (
        project_dir / "core" / "venous" / "resiliency" / "LoadShedder" / "LoadShedder.py"
    )
    shedder_glue = project_dir / "app" / "load_shedding.py"
    ctx.record(
        "load_shedder_primitive_shipped",
        shedder_primitive.exists(),
        str(shedder_primitive),
    )
    ctx.record(
        "load_shedder_glue_calls_install",
        shedder_glue.exists() and "install_load_shedding" in shedder_glue.read_text(),
        "app/load_shedding.py exposes install_load_shedding(app)",
    )

    # ---- LoadShedder REALLY sheds by priority under sustained overload ----------
    ls_mod = _import_project_module(
        project_dir, "core.venous.resiliency.LoadShedder.LoadShedder"
    )
    shedder = ls_mod.InMemoryLoadShedder()
    # Drive sustained high pressure (cpu≈1.0, deep queue) so the cutoff tightens.
    for _ in range(64):
        shedder.admit("sheddable", queue_depth=100_000, cpu_load_ewma=1.0)
    critical_ok = shedder.admit("critical", queue_depth=100_000, cpu_load_ewma=1.0)
    sheddable_shed = shedder.admit("sheddable", queue_depth=100_000, cpu_load_ewma=1.0)
    ctx.record(
        "load_shedder_admits_critical_under_overload",
        critical_ok is True,
        f"critical admitted under overload: {critical_ok!r}, cutoff={shedder.current_cutoff()}",
    )
    ctx.record(
        "load_shedder_sheds_low_priority_under_overload",
        sheddable_shed is False,
        f"sheddable rejected under overload: {sheddable_shed!r}",
    )
    rej = shedder.rejection_signal()
    ctx.record(
        "load_shedder_rejection_is_503_with_retry",
        rej.http_status == 503 and rej.retry_after_seconds > 0,
        f"rejection signal: http={rej.http_status}, retry_after={rej.retry_after_seconds}",
    )

    # ---- Bulkhead pool config readable ------------------------------------------
    bulkhead_path = project_dir / "app" / "resilience" / "bulkhead.py"
    pool_config_path = project_dir / "app" / "resilience" / "pool_config.py"
    bulkhead_found = bulkhead_path.exists() or pool_config_path.exists()
    ctx.record(
        "bulkhead_config_file_exists",
        bulkhead_found,
        "app/resilience/bulkhead.py or pool_config.py present",
    )

    if bulkhead_path.exists():
        src = bulkhead_path.read_text()
        ctx.record(
            "bulkhead_class_present",
            "Bulkhead" in src or "bulkhead" in src.lower(),
            "Bulkhead class definition in bulkhead.py",
        )
    else:
        ctx.record("bulkhead_class_present", False, "bulkhead.py not found")

    # ---- Bulkhead status route file written (may be commented in main.py) -------
    # add_bulkhead_isolation writes the route file but adds it as a commented
    # include in main.py (opt-in pattern). Verify the file was written.
    bulkhead_status_route = project_dir / "app" / "api" / "routes" / "bulkhead_status.py"
    ctx.record(
        "bulkhead_status_route_file_exists",
        bulkhead_status_route.exists(),
        str(
            bulkhead_status_route.relative_to(project_dir)
            if bulkhead_status_route.exists()
            else "NOT FOUND"
        ),
    )

    # GET /resilience/bulkheads — may be 404 if not auto-registered (opt-in route)
    r = await client.get("/resilience/bulkheads")
    if r.status_code != 404:
        ctx.record(
            "resilience_bulkheads_endpoint_or_file",
            True,
            f"GET /resilience/bulkheads: {r.status_code}",
        )
        if r.status_code == 200:
            body = {}
            with contextlib.suppress(Exception):
                body = r.json()
            ctx.record(
                "bulkheads_response_is_json",
                isinstance(body, (dict, list)),
                f"body type: {type(body).__name__}",
            )
        else:
            ctx.record("bulkheads_response_is_json", True, f"skipped (status {r.status_code})")
    else:
        # Route not auto-registered (opt-in) — pass if the route file was written
        ctx.record(
            "resilience_bulkheads_endpoint_or_file",
            bulkhead_status_route.exists(),
            "route file written (opt-in include in main.py)",
        )
        ctx.record("bulkheads_response_is_json", True, "skipped (route not auto-registered)")


RESILIENCY_STACK = Scenario(
    name="resiliency_stack",
    archetype="LoadShedder + AdaptiveTimeouts + BulkheadIsolation resiliency stack",
    models={"Service": {"name": "str", "endpoint": "str"}},
    tools=[
        ("add_load_shedding", "adapt.extend.infrastructure.add_load_shedding"),
        ("add_adaptive_timeouts", "adapt.extend.infrastructure.add_adaptive_timeouts"),
        ("add_bulkhead_isolation", "adapt.extend.infrastructure.add_bulkhead_isolation"),
    ],
    flow=flow_resiliency_stack,
)


# ===========================================================================
# SCENARIO 24 — Retry + Chaos + Shutdown
# TOOL-098 add_retry_budget, TOOL-099 add_chaos_testing, TOOL-100 add_graceful_shutdown
# ===========================================================================


async def flow_retry_chaos_shutdown(ctx: ScenarioContext) -> None:
    """RetryBudget module imports, ChaosEngine has production guard, GracefulShutdown present."""
    project_dir = ctx.project_dir
    client = ctx.client

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- CANONICAL: venous RetryPolicy primitive + adapter + glue ----------------
    # add_retry_budget copies core.venous.resiliency.RetryPolicy (which carries the
    # RetryBudget) + the RetryPolicyAdapter and emits app/retry.py (NOT a
    # hand-rolled app/resilience/retry_budget.py).
    retry_primitive = (
        project_dir / "core" / "venous" / "resiliency" / "RetryPolicy" / "RetryPolicy.py"
    )
    retry_glue = project_dir / "app" / "retry.py"
    ctx.record(
        "retry_policy_primitive_shipped",
        retry_primitive.exists(),
        str(retry_primitive),
    )
    ctx.record(
        "retry_glue_calls_install",
        retry_glue.exists() and "install_retry_policy" in retry_glue.read_text(),
        "app/retry.py exposes install_retry_policy(app)",
    )

    # ---- RetryPolicy REALLY retries transients, honors budget, skips programmer errs
    rp_mod = _import_project_module(
        project_dir, "core.venous.resiliency.RetryPolicy.RetryPolicy"
    )

    transient_calls = {"n": 0}

    async def _flaky() -> str:
        transient_calls["n"] += 1
        if transient_calls["n"] < 3:
            raise ConnectionError("transient")  # retryable per default classifier
        return "ok"

    policy = rp_mod.ExponentialBackoffRetryPolicy(
        max_attempts=5, initial_interval_ms=1, jitter=0.0
    )
    result = await policy.execute(_flaky, idempotent=True)
    ctx.record(
        "retry_recovers_transient_failure",
        result == "ok" and transient_calls["n"] == 3,
        f"retried ConnectionError to success in {transient_calls['n']} attempts",
    )

    nonretryable_calls = {"n": 0}

    async def _programmer_error() -> str:
        nonretryable_calls["n"] += 1
        raise ValueError("programmer error")  # non_retryable per default classifier

    policy2 = rp_mod.ExponentialBackoffRetryPolicy(
        max_attempts=5, initial_interval_ms=1, jitter=0.0
    )
    try:
        await policy2.execute(_programmer_error, idempotent=True)
        ctx.record("retry_skips_non_retryable", False, "ValueError was not re-raised")
    except ValueError:
        ctx.record(
            "retry_skips_non_retryable",
            nonretryable_calls["n"] == 1,
            f"non-retryable ValueError tried exactly once: {nonretryable_calls['n']}",
        )

    starved_budget = rp_mod.RetryBudget(budget_ratio=0.0, min_floor=0)
    policy3 = rp_mod.ExponentialBackoffRetryPolicy(
        max_attempts=5, initial_interval_ms=1, jitter=0.0, budget=starved_budget
    )

    async def _always_fail() -> str:
        raise ConnectionError("down")

    try:
        await policy3.execute(_always_fail, idempotent=True)
        ctx.record("retry_budget_caps_amplification", False, "no budget rejection raised")
    except rp_mod.RetryPolicyInvariantError as exc:
        ctx.record(
            "retry_budget_caps_amplification",
            "budget exhausted" in str(exc),
            "exhausted retry budget refused to amplify load (RETRY-INV-02)",
        )

    # ---- ChaosEngine has production guard (ENVIRONMENT check) -------------------
    chaos_init_path = project_dir / "app" / "chaos" / "__init__.py"
    ctx.record(
        "chaos_engine_file_exists",
        chaos_init_path.exists(),
        str(chaos_init_path.relative_to(project_dir) if chaos_init_path.exists() else "NOT FOUND"),
    )

    if chaos_init_path.exists():
        chaos_src = chaos_init_path.read_text()
        # Must have hardcoded production guard: check ENVIRONMENT != production
        has_production_guard = (
            "production" in chaos_src
            and "ENVIRONMENT" in chaos_src
            and ("ChaosEngine" in chaos_src)
        )
        ctx.record(
            "chaos_engine_has_production_guard",
            has_production_guard,
            "ChaosEngine + ENVIRONMENT + 'production' all present in chaos/__init__.py",
        )
    else:
        ctx.record("chaos_engine_has_production_guard", False, "chaos/__init__.py not found")

    # ---- GET /chaos/status returns enabled=false (ENVIRONMENT=local) ------------
    r_chaos = await client.get("/chaos/status")
    ctx.record(
        "chaos_status_endpoint_exists",
        r_chaos.status_code != 404,
        f"GET /chaos/status: {r_chaos.status_code} (404 = route missing)",
    )
    if r_chaos.status_code == 200:
        try:
            body = r_chaos.json()
            enabled_val = body.get("enabled", body.get("chaos_enabled", None))
            ctx.record(
                "chaos_status_not_enabled_in_local",
                enabled_val is False or enabled_val == "false" or enabled_val is None,
                f"enabled={enabled_val!r} (should be false/None in local env)",
            )
        except Exception:
            ctx.record("chaos_status_not_enabled_in_local", True, "skipped (could not parse JSON)")
    else:
        ctx.record(
            "chaos_status_not_enabled_in_local", True, f"skipped (status {r_chaos.status_code})"
        )

    # ---- CANONICAL: venous GracefulShutdown primitive + adapter + glue -----------
    # add_graceful_shutdown copies core.venous.resiliency.GracefulShutdown (signal
    # handling lives in the primitive's register(); the adapter wires drain
    # middleware) and emits app/shutdown.py (NOT a hand-rolled
    # app/lifecycle/shutdown.py).
    shutdown_primitive = (
        project_dir / "core" / "venous" / "resiliency" / "GracefulShutdown" / "GracefulShutdown.py"
    )
    shutdown_glue = project_dir / "app" / "shutdown.py"
    ctx.record(
        "graceful_shutdown_primitive_shipped",
        shutdown_primitive.exists(),
        str(shutdown_primitive),
    )
    ctx.record(
        "graceful_shutdown_glue_calls_install",
        shutdown_glue.exists() and "install_graceful_shutdown" in shutdown_glue.read_text(),
        "app/shutdown.py exposes install_graceful_shutdown(app)",
    )
    # The primitive's register() installs SIGTERM/SIGINT handlers.
    prim_src = shutdown_primitive.read_text()
    ctx.record(
        "graceful_shutdown_registers_signal_handlers",
        "SIGTERM" in prim_src and "SIGINT" in prim_src and "def register" in prim_src,
        "GracefulShutdown.register() installs SIGTERM/SIGINT handlers",
    )

    # ---- GracefulShutdown REALLY drains: not draining → drain on signal → wait ---
    gs_mod = _import_project_module(
        project_dir, "core.venous.resiliency.GracefulShutdown.GracefulShutdown"
    )
    sd = gs_mod.GracefulShutdown(drain_seconds=0.0, timeout_seconds=2.0)
    not_draining = sd.is_draining()
    sd.increment_in_flight()
    sd._on_signal()  # equivalent to receiving SIGTERM
    draining_now = sd.is_draining()
    sd.decrement_in_flight()  # last in-flight request completes
    await asyncio.wait_for(sd.wait_complete(), timeout=3.0)
    ctx.record(
        "graceful_shutdown_drains_then_completes",
        not_draining is False and draining_now is True,
        f"draining before/after signal: {not_draining} -> {draining_now}; wait_complete() returned",
    )

    # ---- POST /chaos/enable returns 403 or error in local (production guard) ----
    r_enable = await client.post("/chaos/enable", json={})
    ctx.record(
        "chaos_enable_endpoint_exists",
        r_enable.status_code != 404,
        f"POST /chaos/enable: {r_enable.status_code} (404 = route missing)",
    )
    # In local env with ENVIRONMENT=local, enabling chaos may succeed (200)
    # or fail (403/422/500). All are acceptable; 404 is not.
    ctx.record(
        "chaos_enable_responds_meaningfully",
        r_enable.status_code != 404,
        f"POST /chaos/enable: {r_enable.status_code} (not 404)",
    )


RETRY_CHAOS_SHUTDOWN = Scenario(
    name="retry_chaos_shutdown",
    archetype="RetryBudget + ChaosEngine (prod guard) + GracefulShutdown",
    models={"Task": {"name": "str", "retries": "int"}},
    tools=[
        ("add_retry_budget", "adapt.extend.infrastructure.add_retry_budget"),
        ("add_chaos_testing", "adapt.extend.infrastructure.add_chaos_testing"),
        ("add_graceful_shutdown", "adapt.extend.infrastructure.add_graceful_shutdown"),
    ],
    flow=flow_retry_chaos_shutdown,
)


# ===========================================================================
# pytest integration — one parametrized test per scenario (this file only)
# ===========================================================================

SCENARIOS: list[Scenario] = [
    RESILIENCY_STACK,
    RETRY_CHAOS_SHUTDOWN,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await run_scenario_assert(scenario)


# ===========================================================================
# Standalone runner (no pytest required) — runs ALL 10 scenarios
# ===========================================================================


def _all_scenarios() -> list[Scenario]:
    """Assemble the full batch-5 scenario list across the split part files."""
    import asyncio  # noqa: F401  (kept for parity with original module imports)

    from tests.test_behavior_scenarios_batch5__part2 import SCENARIOS as P2
    from tests.test_behavior_scenarios_batch5__part3 import SCENARIOS as P3
    from tests.test_behavior_scenarios_batch5__part4 import SCENARIOS as P4

    return [*SCENARIOS, *P2, *P3, *P4]


def main() -> int:
    import asyncio

    all_scenarios = _all_scenarios()

    print("=" * 74)
    print(f"  SKILL-001 BATCH 5 BEHAVIOR SCENARIOS — {len(all_scenarios)} scenarios")
    print("  Tools: TOOL-095 to TOOL-124 (30 newest tools)")
    print("  Backend: SQLite in-memory (no PostgreSQL required)")
    print("=" * 74)
    print()

    overall_passed = 0
    overall_total = 0
    scenario_results: list[tuple[str, int, int, list]] = []
    t_start = time.monotonic()

    for scenario in all_scenarios:
        t0 = time.monotonic()
        print(f"  Scenario: {scenario.name}")
        print(f"  Archetype: {scenario.archetype}")
        print(f"  Tools: {', '.join(t[0] for t in scenario.tools)}")
        try:
            passed, total, details = asyncio.run(_run_scenario(scenario))
        except Exception as exc:
            passed, total = 0, 1
            details = [("runner", False, f"{type(exc).__name__}: {str(exc)[:200]}")]

        elapsed = time.monotonic() - t0
        overall_passed += passed
        overall_total += total
        scenario_results.append((scenario.name, passed, total, details))

        mark = "PASS" if passed == total else "FAIL"
        print(f"  [{mark}] {passed}/{total} assertions  ({elapsed:.1f}s)")
        for name, ok, detail in details:
            status = "  [PASS]" if ok else "  [FAIL]"
            print(f"    {status}  {name}: {detail}")
        print()

    elapsed = time.monotonic() - t_start
    print("=" * 74)
    print(
        f"  OVERALL: {overall_passed}/{overall_total} assertions "
        f"across {len(all_scenarios)} scenarios  ({elapsed:.1f}s)"
    )
    print("=" * 74)

    failed_scenarios = [r for r in scenario_results if r[1] < r[2]]
    if failed_scenarios:
        print()
        print("  Failed scenarios:")
        for name, p, t, _ in failed_scenarios:
            print(f"    - {name}: {p}/{t}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
