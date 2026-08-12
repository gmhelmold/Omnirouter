"""Performance baseline tests.

These tests lock ceilings for hot paths so regressions surface as test
failures instead of silent slowdowns. Numbers are generous (≥ 2x observed
median) to avoid flakiness while still catching order-of-magnitude
regressions.

Subprocess hot paths are measured by **CPU time (user+sys)**, not
wall-clock — see ``_cpu_subprocess``. This makes the suite robust on a
shared / loaded machine (a busy co-tenant no longer produces false reds)
while still catching real algorithmic regressions. The in-process catalog
parse stays wall-clock (it is sub-second and load-immune).

Measured paths (bounds are the `BOUND_*` constants below —
mirror with the constants, not the docstring; constants win on
drift):

    1. Catalog load + parse                → BOUND_CATALOG_LOAD      (1.0s)
    2. Classifier run over pool            → BOUND_CLASSIFIER_RUN    (60.0s)
    3. Ledger Markdown render              → BOUND_LEDGER_RENDER     (5.0s)
    4. Contract check full run             → BOUND_CONTRACT_CHECK    (90.0s)
    5. Catalog manifest verify idempotent  → BOUND_MANIFEST_VERIFY   (30.0s)

If a real regression surfaces, the remedy is to either
    (a) find the slow path and fix it, OR
    (b) document why the new baseline is acceptable and update the
        ceiling with a commit message citing the rationale.

Bypassing via pytest.mark.skip is forbidden without a CHANGELOG note.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]


# Upper bounds. catalog-load is WALL seconds (in-process); the four
# subprocess bounds are CPU seconds (user+sys, via _cpu_subprocess).
# Generous vs observed median so noise doesn't flap; still tight enough to
# catch a real regression.
#
# Observed CPU on this machine even under load avg ~190 (Jun 2026):
#   catalog load (wall) ≈ 0.02s
#   classifier run      ≈ 43s CPU  (≈17s idle)
#   ledger render       ≈ 0.6s CPU
#   contract check      ≈ 65s CPU  (≈38s idle; B1.6 orphan-generator scan dominates)
#   manifest verify     ≈ 8.6s CPU (≈6s idle)
#
# Known optimisation opportunity: CONTRACT §B1.6 can be sped up to
# < 5s with a single-pass AST scan over `generators/` — tracked for
# v1.1 post-release.
BOUND_CATALOG_LOAD = 1.0
BOUND_CLASSIFIER_RUN = 60.0
BOUND_LEDGER_RENDER = 5.0
BOUND_CONTRACT_CHECK = 90.0
BOUND_MANIFEST_VERIFY = 30.0


def _python_bin() -> str:
    """Prefer the skill's venv python; fall back to the current interpreter."""
    venv_py = SKILL_ROOT / ".venv" / "bin" / "python"
    return str(venv_py) if venv_py.exists() else sys.executable


def _cpu_subprocess(cmd: list[str]) -> float:
    """Return the subprocess's CPU time (user+sys seconds).

    Measures CPU time, NOT wall-clock. A performance-regression test must
    track the *work* a hot path does — that is what catches an algorithmic
    regression (an O(n^2) scan burns more CPU regardless of who else is on
    the box). Wall-clock conflates work with scheduler wait, so on a shared
    / loaded machine it produces false reds — an unchanged code path
    "fails" simply because a co-tenant is busy — while catching nothing a
    CPU measurement wouldn't. CPU time is invariant to co-tenant load.

    Empirically, on this machine under load average ~190, the contract-check
    subprocess took 153s wall but only 65s CPU (~= its idle wall-clock);
    classify took 142s wall / 43s CPU. The bounds below are CPU seconds.

    Inherits the current env and overrides PYTHONPATH so imports resolve
    against the skill tree. Fails the test if the subprocess errored.
    """
    import os
    import tempfile

    env = {**os.environ, "PYTHONPATH": str(SKILL_ROOT)}
    with tempfile.TemporaryFile(mode="w+") as errf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(SKILL_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=errf,
            env=env,
            text=True,
        )
        _pid, status, rusage = os.wait4(proc.pid, 0)
        cpu = rusage.ru_utime + rusage.ru_stime
        rc = os.waitstatus_to_exitcode(status)
        if rc != 0:
            errf.seek(0)
            pytest.fail(f"subprocess failed ({rc}):\ncmd={cmd}\nstderr={errf.read()[-2000:]}")
    return cpu


def test_catalog_load_under_bound():
    """Reading + JSON-parsing catalog.json is sub-second."""
    catalog_path = SKILL_ROOT / "engine" / "index" / "catalog.json"
    assert catalog_path.exists(), "catalog missing — run `engine.index.manifest build`"
    start = time.monotonic()
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    elapsed = time.monotonic() - start
    assert "tools" in data, "malformed catalog"
    assert elapsed < BOUND_CATALOG_LOAD, (
        f"catalog load took {elapsed:.3f}s (bound {BOUND_CATALOG_LOAD}s). "
        "Investigate parser / file growth."
    )


def test_classifier_run_under_bound():
    """Full ledger regeneration over the staged pool stays under the ceiling."""
    elapsed = _cpu_subprocess([_python_bin(), "-m", "engine.promotion.classify"])
    assert elapsed < BOUND_CLASSIFIER_RUN, (
        f"classify took {elapsed:.3f}s (bound {BOUND_CLASSIFIER_RUN}s). "
        "Investigate signal scan or measurement hot path."
    )


def test_ledger_render_under_bound():
    """Markdown render from ledger.json."""
    elapsed = _cpu_subprocess([_python_bin(), "-m", "engine.promotion.ledger"])
    assert elapsed < BOUND_LEDGER_RENDER, (
        f"ledger render took {elapsed:.3f}s (bound {BOUND_LEDGER_RENDER}s)."
    )


def test_contract_check_under_bound():
    """Full contract-check harness run (all rules in RULES tuple)."""
    elapsed = _cpu_subprocess([_python_bin(), "-m", "engine.audit.contract_check"])
    assert elapsed < BOUND_CONTRACT_CHECK, (
        f"contract_check took {elapsed:.3f}s (bound {BOUND_CONTRACT_CHECK}s). "
        "A new rule may have introduced a slow path."
    )


def test_manifest_verify_under_bound():
    """Two full catalog builds (verify mode) complete within bound."""
    elapsed = _cpu_subprocess([_python_bin(), "-m", "engine.index.manifest", "verify"])
    assert elapsed < BOUND_MANIFEST_VERIFY, (
        f"manifest verify took {elapsed:.3f}s (bound {BOUND_MANIFEST_VERIFY}s). "
        "Catalog-scan hot path may have regressed."
    )
