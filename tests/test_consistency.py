"""test_consistency.py — BRUTAL hardening round 4C (split shim).

Cross-tool consistency tests: import cycles, model sanity, schema hygiene,
route conflicts, migration chain integrity, and config coverage.

This file was split into ≤500-LOC sibling modules to stay under the repo's
file-size cap. The actual tests and helpers now live in:

  - tests/test_consistency__shared.py  — path setup, 10-tool fixture, AST helpers
  - tests/test_consistency__part1.py   — Test 1 (import cycles) + Test 2 (models)
  - tests/test_consistency__part2.py   — Test 3 (schemas) + Test 4 (routes)
  - tests/test_consistency__part3.py   — Test 5 (migrations) + Test 6 (config) + runner

This suite is a **script-runner** (same convention as ``tests/test_boot.py``):
the check functions in the sibling part files return ``(ok, message)`` tuples
and are driven by the part3 ``run_all_tests()`` runner — they are NOT pytest
tests, so they are named ``check_*`` rather than ``test_*`` to keep pytest from
collecting them.

Run standalone (delegates to the part3 runner):
    PYTHONPATH=. python3 tests/test_consistency.py

Exit code 0  → all 6 test groups pass
Exit code 1  → one or more groups failed (details printed)
"""

from __future__ import annotations

import sys

from tests.test_consistency__part3 import run_all_tests  # noqa: F401,E402

if __name__ == "__main__":
    passed = run_all_tests()
    print(f"\nConsistency: {passed}/6 tests pass")
    sys.exit(0 if passed == 6 else 1)
