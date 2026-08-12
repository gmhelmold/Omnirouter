"""BEHAVIOR scenarios for the 15 NEW tools (scenarios 13-22).

This module was split for the ≤500-LOC cap. The collected pytest tests now
live in sibling files:

  * ``test_behavior_scenarios_new_tools__shared.py`` — Scenario framework +
    helpers (no collected tests).
  * ``test_behavior_scenarios_new_tools__part1.py``  — scenarios 13-16.
  * ``test_behavior_scenarios_new_tools__part2.py``  — scenarios 17-19.
  * ``test_behavior_scenarios_new_tools__part3.py``  — scenarios 20-22.

This file no longer defines any ``test_*`` function (so pytest collects each
scenario exactly once, from the part files). It retains the standalone runner
(``python tests/test_behavior_scenarios_new_tools.py``) which aggregates the
scenarios from all part files.

Each scenario generates its own fixture project with ONLY the tools it
needs, patches ``app/core/db.py`` to SQLite+aiosqlite (no Docker),
patches ``app/middleware/idempotency.py`` to a pass-through stub, boots
the app, and runs real HTTP flows.

These tests intentionally do NOT require PostgreSQL — they use SQLite so
that CI can run them with zero external infrastructure.

Run::

    PYTHONPATH=. .venv/bin/pytest tests/test_behavior_scenarios_new_tools__part1.py -v
    PYTHONPATH=. .venv/bin/python tests/test_behavior_scenarios_new_tools.py  # standalone

Exit 0 → all assertions passed.
Exit 1 → at least one scenario has failures.
"""

from __future__ import annotations

import asyncio
import sys
import time

from tests.test_behavior_scenarios_new_tools__part1 import (
    SCENARIOS as _SCENARIOS_1,
)
from tests.test_behavior_scenarios_new_tools__part2 import (
    SCENARIOS as _SCENARIOS_2,
)
from tests.test_behavior_scenarios_new_tools__part3 import (
    SCENARIOS as _SCENARIOS_3,
)
from tests.test_behavior_scenarios_new_tools__shared import Scenario, _run_scenario

# ===========================================================================
# Scenario registry (aggregated from all part files)
# ===========================================================================

SCENARIOS: list[Scenario] = [*_SCENARIOS_1, *_SCENARIOS_2, *_SCENARIOS_3]


# ===========================================================================
# Standalone runner (no pytest required)
# ===========================================================================


def main() -> int:
    print("=" * 74)
    print(f"  SKILL-001 NEW TOOL BEHAVIOR SCENARIOS — {len(SCENARIOS)} scenarios")
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
