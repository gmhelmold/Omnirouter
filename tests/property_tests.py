"""Property-based tests for adapt tool contracts.

Tests properties that must hold for ANY adapt tool, regardless of which
tool is being tested. Uses randomized inputs within valid bounds.

No Hypothesis required — uses random + loops (100 iterations) per property.

Run with::

    PYTHONPATH=. python3 tests/property_tests.py

This module is the runnable entry point. The property definitions and shared
infrastructure live in sibling modules to stay under the 500-LOC cap:
  - tests/property_tests__helpers.py — tool registry, helpers, _Results
  - tests/property_tests__part1.py   — properties 1-4
  - tests/property_tests__part2.py   — properties 5-8
"""

from __future__ import annotations

import sys

from tests.property_tests__helpers import _load_all_tools
from tests.property_tests__part1 import (
    prop_dry_run_purity,
    prop_error_on_invalid_dir,
    prop_idempotency,
    prop_parse_correctness,
)
from tests.property_tests__part2 import (
    prop_lazy_sdk_imports,
    prop_ruff_critical_clean,
    prop_standalone_mode,
    prop_toolresult_contract,
)

# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_all_properties() -> int:
    """Run all 5 properties against every registered tool.

    Returns:
        Exit code: 0 if all properties passed, 1 otherwise.
    """
    tools = _load_all_tools()
    n_tools = len(tools)

    print(f"\n{'=' * 70}")
    print(f"PROPERTY-BASED TESTS  —  {n_tools} tools × 8 properties")
    print(f"{'=' * 70}\n")

    properties = [
        prop_idempotency(tools),
        prop_dry_run_purity(tools),
        prop_error_on_invalid_dir(tools),
        prop_parse_correctness(tools),
        prop_toolresult_contract(tools),
        prop_ruff_critical_clean(tools),
        prop_lazy_sdk_imports(tools),
        prop_standalone_mode(tools),
    ]

    overall_ok = True
    for res in properties:
        print(res.summary())
        if not res.ok:
            overall_ok = False

    print(f"\n{'=' * 70}")
    passed_props = sum(1 for r in properties if r.ok)
    total_props = len(properties)
    total_tool_checks = sum(r.passed + r.failed for r in properties)
    total_passed = sum(r.passed for r in properties)

    if overall_ok:
        print(
            f"RESULT: ALL PASSED — "
            f"{total_props}/{total_props} properties × {n_tools} tools "
            f"({total_passed}/{total_tool_checks} tool-checks passed)"
        )
    else:
        print(
            f"RESULT: FAILED — "
            f"{passed_props}/{total_props} properties passed "
            f"({total_passed}/{total_tool_checks} tool-checks passed)"
        )
    print(f"{'=' * 70}\n")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(run_all_properties())
