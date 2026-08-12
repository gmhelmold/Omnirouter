"""BEHAVIOR scenarios 33-38 — gap-fill for 10 tools with ZERO runtime coverage.

This module was split to respect the 500-LOC cap. The collectable pytest
tests now live in sibling files; this file carries NO collectable tests of
its own (its ``main()`` runner is invoked only as ``__main__``):

  * ``test_behavior_scenarios_gap_fill__shared.py`` — framework + driver
  * ``test_behavior_scenarios_gap_fill__part1.py``  — scenarios 33-34
  * ``test_behavior_scenarios_gap_fill__part2.py``  — scenarios 35-36
  * ``test_behavior_scenarios_gap_fill__part3.py``  — scenarios 37-38

Each scenario generates its own fixture project with ONLY the tools it
needs, patches ``app/core/db.py`` to SQLite+aiosqlite (no Docker),
patches ``app/middleware/idempotency.py`` to a pass-through stub, boots
the app (where feasible), and runs real HTTP flows or file-content checks.

These tests intentionally do NOT require PostgreSQL — they use SQLite so
that CI can run them with zero external infrastructure.

Run via pytest (collects the __partN siblings)::

    PYTHONPATH=. .venv/bin/pytest tests/test_behavior_scenarios_gap_fill__part1.py \\
        tests/test_behavior_scenarios_gap_fill__part2.py \\
        tests/test_behavior_scenarios_gap_fill__part3.py -v

Or run the standalone aggregate runner (no pytest required)::

    PYTHONPATH=. .venv/bin/python tests/test_behavior_scenarios_gap_fill.py

Exit 0 → all assertions passed.
Exit 1 → at least one scenario has failures.
"""

from __future__ import annotations

import sys
import time

from tests.test_behavior_scenarios_gap_fill__part1 import CQRS, CSRF_AND_SANITIZATION
from tests.test_behavior_scenarios_gap_fill__part2 import DATA_PIPELINE, MIGRATIONS_CI
from tests.test_behavior_scenarios_gap_fill__part3 import PUSH_AND_EMAIL, RATE_LIMITING
from tests.test_behavior_scenarios_gap_fill__shared import Scenario, _run_scenario

# ===========================================================================
# Scenario registry (aggregate — all parts)
# ===========================================================================

SCENARIOS: list[Scenario] = [
    CQRS,
    CSRF_AND_SANITIZATION,
    DATA_PIPELINE,
    MIGRATIONS_CI,
    PUSH_AND_EMAIL,
    RATE_LIMITING,
]


# ===========================================================================
# Standalone runner (no pytest required)
# ===========================================================================


def main() -> int:
    import asyncio

    print("=" * 74)
    print(f"  SKILL-001 GAP-FILL BEHAVIOR SCENARIOS — {len(SCENARIOS)} scenarios")
    print("  Backend: SQLite in-memory (no PostgreSQL required)")
    print("=" * 74)
    print()

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
