"""BEHAVIOR scenarios — exercise SKILL-001 across 7 distinct domains.

Where the other tests verify "can SKILL-001 produce code that boots?",
this file verifies "does SKILL-001 produce code that *behaves correctly*
for a specific business domain?". Each scenario:

1. Defines realistic domain models (e.g., Healthcare: Patient, Visit, Rx)
2. Applies ONLY the tools a domain practitioner would choose
3. Runs a domain-specific flow (5-15 HTTP calls)
4. Asserts domain-specific invariants (e.g., HIPAA: every patient
   read must be captured in audit_logs)

The 7 scenarios cover different "archetypes" of FastAPI applications —
collectively they exercise all 27 adapt tools in realistic combinations
so regressions in any tool surface here before they hit production.

Requires PostgreSQL 16 on localhost:54329 (see test_e2e_postgres.py).

Run:
    PYTHONPATH=. .venv/bin/python tests/test_behavior_scenarios.py

This module is the runner. The scenario framework + shared helpers live in
test_behavior_scenarios__shared.py; the individual scenario definitions live
in test_behavior_scenarios__part1.py (scenarios 1-6),
test_behavior_scenarios__part2.py (scenarios 7-10) and
test_behavior_scenarios__part3.py (scenarios 11-12). They are imported here so
the file stays under the 500-LOC cap without changing behaviour.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
import time
import traceback
from pathlib import Path

from tests.test_behavior_scenarios__part1 import (
    ANALYTICS,
    BLOG,
    ECOMMERCE,
    FINTECH,
    HEALTHCARE,
    SAAS_B2B,
)
from tests.test_behavior_scenarios__part2 import (
    ARQ_WORKER,
    MODERATION,
    STRIPE_CHECKOUT,
    WEBSOCKET_CHAT,
)
from tests.test_behavior_scenarios__part3 import (
    EMAIL_TEMPLATES,
    SQLADMIN,
)

# Importing the shared module FIRST runs the os.environ.setdefault(...) calls
# before any application module is imported.
from tests.test_behavior_scenarios__shared import (
    POSTGRES_URL,
    Scenario,
    ScenarioContext,
    _make_client,
    _precheck_postgres,
    _teardown,
)

# ---------------------------------------------------------------------------
# Registry + runner
# ---------------------------------------------------------------------------

SCENARIOS: list[Scenario] = [
    ECOMMERCE,
    SAAS_B2B,
    HEALTHCARE,
    FINTECH,
    BLOG,
    ANALYTICS,
    MODERATION,
    WEBSOCKET_CHAT,
    ARQ_WORKER,
    STRIPE_CHECKOUT,
    EMAIL_TEMPLATES,
    SQLADMIN,
]


def _build_project(scenario: Scenario, tmp: Path) -> Path:
    """Generate project with scenario models and apply its tools."""
    from adapt.contracts import ToolInput
    from tests.common.fixture_factory import create_fixture_project

    project_dir = create_fixture_project(
        name=f"scn_{scenario.name}",
        models=scenario.models,
        tmp_dir=tmp,
    )

    for tool_name, mod_path in scenario.tools:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, tool_name)
        result = fn(ToolInput(project_dir=str(project_dir)))
        if result.status == "error":
            raise RuntimeError(f"{tool_name}: {result.error}")

    return project_dir


async def _run_scenario(scenario: Scenario) -> tuple[int, int, list[tuple[str, bool, str]]]:
    """Run a single scenario end-to-end. Return (passed, total, details)."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            project_dir = _build_project(scenario, Path(tmp))
        except Exception as exc:
            return 0, 1, [("generate_and_apply", False, f"{type(exc).__name__}: {str(exc)[:200]}")]

        try:
            app, client, session, engine = await _make_client(
                project_dir,
                scenario.tenant_slug,
                scenario.needs_multi_tenancy,
            )
        except Exception as exc:
            return 0, 1, [("boot", False, f"{type(exc).__name__}: {str(exc)[:200]}")]

        ctx = ScenarioContext(
            client=client,
            session=session,
            engine=engine,
            project_dir=project_dir,
            tenant_slug=scenario.tenant_slug,
        )
        try:
            await scenario.flow(ctx)
        except Exception as exc:
            ctx.record("flow", False, f"EXCEPTION: {type(exc).__name__}: {str(exc)[:200]}")
            traceback.print_exc()
        finally:
            await _teardown(app, client, session, engine)

    passed = sum(1 for _, ok, _ in ctx.report_section if ok)
    total = len(ctx.report_section)
    return passed, total, ctx.report_section


def main() -> int:
    print("=" * 74)
    print(f"  SKILL-001 BEHAVIOR SCENARIOS — {len(SCENARIOS)} domain archetypes")
    print(f"  PostgreSQL: {POSTGRES_URL}")
    print("=" * 74)
    print()

    if not asyncio.run(_precheck_postgres()):
        print("  [SKIP] PostgreSQL not reachable")
        return 2

    overall_passed = 0
    overall_total = 0
    scenario_results: list[tuple[str, int, int, list]] = []
    t_start = time.monotonic()

    for scenario in SCENARIOS:
        t0 = time.monotonic()
        print(f"▶ {scenario.name} ({scenario.archetype})")
        print(f"  tools: {', '.join(t[0] for t in scenario.tools)}")
        try:
            passed, total, details = asyncio.run(_run_scenario(scenario))
        except Exception as exc:
            passed, total = 0, 1
            details = [("runner", False, f"{type(exc).__name__}: {str(exc)[:200]}")]

        elapsed = time.monotonic() - t0
        overall_passed += passed
        overall_total += total
        scenario_results.append((scenario.name, passed, total, details))

        mark = "✓" if passed == total else "✗"
        print(f"  {mark} {passed}/{total} assertions passed  ({elapsed:.1f}s)")
        for name, ok, detail in details:
            status = "  [PASS]" if ok else "  [FAIL]"
            print(f"    {status}  {name}: {detail}")
        print()

    elapsed = time.monotonic() - t_start
    print("=" * 74)
    print(
        f"  OVERALL: {overall_passed}/{overall_total} assertions "
        f"across {len(SCENARIOS)} scenarios  ({elapsed:.1f}s)"
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
