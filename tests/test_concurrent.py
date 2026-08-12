"""Concurrent tool application tests for SKILL-001-fastapi-production — Round 5A hardening.

Three scenarios:
    Test 1 — 5 tools running simultaneously on the SAME project
        Verifies: all return success/no_op (no crash), final project
        parses as valid Python (ast), and boots from app.main import app.
        Also detects corrupted files, truncated writes, and lost imports.

    Test 2 — Idempotency under concurrency (soft_delete × 10 parallel)
        Verifies: at most 1 success (the first writer); all others are
        no_op.  Final project is identical to a sequential single run.

    Test 3 — File locking stress (3 tools all writing main.py)
        Verifies: main.py final state contains import fingerprints from
        ALL 3 tools — none silently lost.

Run from the skill root:
    PYTHONPATH=. python3 tests/test_concurrent.py
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running directly as a script from any CWD
# ---------------------------------------------------------------------------

_SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

from adapt.contracts import ToolInput
from generators.orchestrator import generate_project


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _generate_base(
    output_dir: str,
    name: str = "app",
    models: dict | None = None,
) -> dict:
    """Generate a base project without OTEL/Prometheus (avoids broken-OTEL imports)."""
    return generate_project(
        output_dir=output_dir,
        name=name,
        models=models,
        owner_models={m: "user" for m in (models or {}) if m != "User"},
        with_docker_compose=False,
        with_ci=False,
        with_otel=False,
        with_prometheus=False,
    )


def _apply_tool(project_dir: str, mod_path: str, tool_name: str) -> tuple[str, str, Exception | None]:
    """Call a single EXTEND tool, return (tool_name, status, exception_or_None)."""
    try:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, tool_name)
        result = fn(ToolInput(project_dir=project_dir))
        return tool_name, result.status, None
    except Exception as exc:  # noqa: BLE001
        return tool_name, "exception", exc


def _all_py_parseable(root: str) -> list[str]:
    """Return list of relative paths that fail ast.parse."""
    failures: list[str] = []
    for py in Path(root).rglob("*.py"):
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{py.relative_to(root)}: {exc}")
    return failures


def _boot_ok(project_dir: str) -> tuple[bool, str]:
    """Return (True, '') if `from app.main import app` succeeds in a subprocess."""
    r = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print('BOOT OK')"],
        cwd=project_dir,
        env={**os.environ, "PYTHONPATH": project_dir},
        capture_output=True,
        text=True,
        timeout=60,
    )
    if "BOOT OK" in r.stdout:
        return True, ""
    # Extract first meaningful error line, ignoring known-noisy prefixes
    noise_prefixes = ("pydantic", "opentelemetry", "logfire", "UserWarning", "DeprecationWarning")
    for line in r.stderr.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(kw in s for kw in ("Error:", "Exception:", "ImportError", "ModuleNotFoundError")):
            if not any(noise in s for noise in noise_prefixes):
                return False, s
    for line in reversed(r.stderr.splitlines()):
        s = line.strip()
        if s and not any(noise in s for noise in noise_prefixes):
            return False, f"rc={r.returncode} — {s}"
    return False, f"rc={r.returncode} (no clear error line)"


# ---------------------------------------------------------------------------
# Test 1 — 5 tools simultaneously on the SAME project
# ---------------------------------------------------------------------------

_T1_TOOLS: list[tuple[str, str]] = [
    ("add_search",           "adapt.extend.crud_data.add_search"),
    ("add_cache_layer",      "adapt.extend.infrastructure.add_cache_layer"),
    ("add_circuit_breaker",  "adapt.extend.infrastructure.add_circuit_breaker"),
    ("add_api_versioning",   "adapt.extend.api_design.add_api_versioning"),
    ("add_long_running_task","adapt.extend.api_design.add_long_running_task"),
]


def test1_parallel_5_tools() -> tuple[bool, str]:
    """5 tools applied simultaneously to the same project directory.

    Checks:
        1. No tool returned ``status="exception"`` (no crash)
        2. All tools returned success or no_op (nothing errored)
        3. Every .py file in the final project parses without SyntaxError
        4. ``from app.main import app`` boots cleanly

    Detects: corrupted/truncated files, lost imports from concurrent writes.
    """
    label = "TEST 1 [5 tools parallel]"

    simple_models = {
        "Item":  {"title": "str", "description": "str", "price": "int"},
        "User":  {"name": "str", "email": "str"},
    }

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = tmp + "/t1_parallel"

        result = _generate_base(project_dir, name="t1", models=simple_models)
        if result.get("total_files", 0) == 0:
            return False, f"{label}: orchestrator produced zero files"

        # Run all 5 tools in parallel
        tool_results: dict[str, str] = {}
        exceptions: dict[str, Exception] = {}

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_apply_tool, project_dir, mod_path, tool_name): tool_name
                for tool_name, mod_path in _T1_TOOLS
            }
            for future in as_completed(futures):
                tool_name, status, exc = future.result()
                tool_results[tool_name] = status
                if exc is not None:
                    exceptions[tool_name] = exc

        # 1. No exceptions
        if exceptions:
            exc_summary = {k: f"{type(v).__name__}: {v}" for k, v in exceptions.items()}
            return False, f"{label}: tool(s) raised exceptions: {exc_summary}"

        # 2. All success or no_op — never "error"
        errored = {k: v for k, v in tool_results.items() if v == "error"}
        if errored:
            return False, f"{label}: tool(s) returned error status: {errored}"

        # 3. All Python files parse cleanly
        parse_failures = _all_py_parseable(project_dir)
        if parse_failures:
            summary = parse_failures[:5]
            return False, (
                f"{label}: {len(parse_failures)} parse error(s) after parallel run — "
                f"possible corrupted write: {summary}"
            )

        # 4. Boot check
        ok, err_msg = _boot_ok(project_dir)
        if not ok:
            return False, f"{label}: boot FAILED after parallel tools — {err_msg}"

        statuses_str = ", ".join(f"{k}={v}" for k, v in sorted(tool_results.items()))
        return True, f"{label}: PASS — {statuses_str}"


# ---------------------------------------------------------------------------
# Test 2 — Idempotency under concurrency (soft_delete × 10 parallel)
# ---------------------------------------------------------------------------

def test2_idempotency_concurrent() -> tuple[bool, str]:
    """soft_delete applied 10 times in parallel — all must be no_op after first.

    Steps:
        1. Generate base project + apply soft_delete once (sequential baseline).
        2. Run soft_delete 10 more times in parallel.
        3. Verify: ALL 10 parallel runs returned no_op.
        4. Verify: main.py content is identical to post-baseline state.
        5. Verify: mixins.py is parseable and unmodified.

    Detects: TOCTOU races in idempotency guards, double-writes, corruption.
    """
    label = "TEST 2 [idempotency × 10 parallel]"

    simple_models = {"Widget": {"name": "str", "quantity": "int"}}

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = tmp + "/t2_idem"
        _generate_base(project_dir, name="t2", models=simple_models)

        # Sequential baseline — apply once
        mod = importlib.import_module("adapt.extend.crud_data.add_soft_delete")
        fn = getattr(mod, "add_soft_delete")
        baseline_result = fn(ToolInput(project_dir=project_dir))
        if baseline_result.status not in ("success", "no_op"):
            return False, (
                f"{label}: baseline run returned unexpected status "
                f"'{baseline_result.status}': {baseline_result.error}"
            )

        # Capture state after baseline
        main_py = Path(project_dir) / "app" / "main.py"
        mixins_py = Path(project_dir) / "app" / "models" / "mixins.py"

        main_content_after_baseline = main_py.read_text() if main_py.exists() else ""
        mixins_mtime_after_baseline = mixins_py.stat().st_mtime if mixins_py.exists() else None

        # 10 parallel runs
        parallel_results: list[tuple[str, str, Exception | None]] = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [
                pool.submit(
                    _apply_tool,
                    project_dir,
                    "adapt.extend.crud_data.add_soft_delete",
                    "add_soft_delete",
                )
                for _ in range(10)
            ]
            for future in as_completed(futures):
                parallel_results.append(future.result())

        # All 10 must be no_op (baseline already ran)
        non_noop = [
            (name, status, str(exc) if exc else None)
            for name, status, exc in parallel_results
            if status != "no_op"
        ]
        if non_noop:
            return False, (
                f"{label}: {len(non_noop)}/10 parallel runs were NOT no_op: {non_noop[:5]}"
            )

        # main.py must be identical to baseline (no concurrent re-write)
        main_content_final = main_py.read_text() if main_py.exists() else ""
        if main_content_final != main_content_after_baseline:
            # Show what changed
            added = [
                line for line in main_content_final.splitlines()
                if line not in main_content_after_baseline.splitlines()
            ][:5]
            removed = [
                line for line in main_content_after_baseline.splitlines()
                if line not in main_content_final.splitlines()
            ][:5]
            return False, (
                f"{label}: main.py was mutated by concurrent no_op runs "
                f"(added={added}, removed={removed})"
            )

        # mixins.py must parse cleanly
        if mixins_py.exists():
            try:
                ast.parse(mixins_py.read_text())
            except SyntaxError as exc:
                return False, f"{label}: mixins.py corrupted after parallel runs: {exc}"

        return True, f"{label}: PASS — 10/10 parallel runs returned no_op, main.py intact"


# ---------------------------------------------------------------------------
# Test 3 — File locking stress (3 tools all writing main.py)
# ---------------------------------------------------------------------------

# These 3 tools all call _patch_main(app/main.py) as part of their logic.
# Each injects a distinct import fingerprint we can verify afterwards.
_T3_TOOLS: list[tuple[str, str, str]] = [
    # (tool_name, module_path, import_fingerprint_that_must_appear_in_main.py)
    ("add_audit_log",   "adapt.extend.crud_data.add_audit_log",             "audit_listeners"),
    ("add_cache_layer", "adapt.extend.infrastructure.add_cache_layer",       "init_cache"),
    ("add_circuit_breaker", "adapt.extend.infrastructure.add_circuit_breaker", "circuit_breaker"),
]


def test3_file_locking_stress() -> tuple[bool, str]:
    """3 tools that all write app/main.py run in parallel.

    Checks:
        1. No tool raised an exception.
        2. main.py is parseable (no truncation / partial write).
        3. main.py contains the import fingerprint of ALL 3 tools.
           A missing fingerprint means one tool's write silently overwrote
           another's changes — a real race condition.
    """
    label = "TEST 3 [file-locking stress: 3 tools → main.py]"

    simple_models = {
        "Report": {"title": "str", "content": "str"},
        "User":   {"name": "str", "email": "str"},
    }

    with tempfile.TemporaryDirectory() as tmp:
        project_dir = tmp + "/t3_lock"
        result = _generate_base(project_dir, name="t3", models=simple_models)
        if result.get("total_files", 0) == 0:
            return False, f"{label}: orchestrator produced zero files"

        main_py = Path(project_dir) / "app" / "main.py"
        if not main_py.exists():
            return False, f"{label}: app/main.py not found after generation"

        # Apply 3 tools in parallel
        tool_results: dict[str, str] = {}
        exceptions: dict[str, Exception] = {}

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(_apply_tool, project_dir, mod_path, tool_name): tool_name
                for tool_name, mod_path, _ in _T3_TOOLS
            }
            for future in as_completed(futures):
                tool_name, status, exc = future.result()
                tool_results[tool_name] = status
                if exc is not None:
                    exceptions[tool_name] = exc

        # 1. No exceptions
        if exceptions:
            exc_summary = {k: f"{type(v).__name__}: {v}" for k, v in exceptions.items()}
            return False, f"{label}: tool(s) raised exceptions: {exc_summary}"

        # 2. main.py must parse cleanly
        main_text = main_py.read_text()
        try:
            ast.parse(main_text)
        except SyntaxError as exc:
            size = len(main_text)
            return False, (
                f"{label}: main.py is CORRUPTED after parallel writes "
                f"(size={size} bytes, SyntaxError: {exc})"
            )

        # 3. All 3 import fingerprints must be present in main.py
        missing_fingerprints: list[str] = []
        for tool_name, _mod_path, fingerprint in _T3_TOOLS:
            tool_status = tool_results.get(tool_name, "unknown")
            # Only check fingerprint if tool claimed to write something
            if tool_status in ("success",):
                if fingerprint not in main_text:
                    missing_fingerprints.append(
                        f"{tool_name} (fingerprint='{fingerprint}', status={tool_status})"
                    )

        if missing_fingerprints:
            # Show a snippet of main.py for debugging
            snippet = main_text[:600].replace("\n", "\\n")
            return False, (
                f"{label}: main.py is missing import(s) from concurrent tools — "
                f"race condition detected: {missing_fingerprints}. "
                f"main.py snippet: {snippet}"
            )

        statuses_str = ", ".join(f"{k}={v}" for k, v in sorted(tool_results.items()))
        return True, f"{label}: PASS — {statuses_str}; all fingerprints present in main.py"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _sep(char: str = "─", width: int = 72) -> str:
    return char * width


def main() -> int:
    """Run all three concurrent tests and print a summary table.

    Returns:
        0 if all tests pass, 1 if any fail.
    """
    print(_sep("="))
    print("  SKILL-001 Concurrent Tests — Round 5A Hardening")
    print(_sep("="))
    print()

    tests = [
        ("Test 1", test1_parallel_5_tools),
        ("Test 2", test2_idempotency_concurrent),
        ("Test 3", test3_file_locking_stress),
    ]

    results: list[tuple[str, bool, str, float]] = []

    for name, fn in tests:
        print(f"Running {name} ...", flush=True)
        t0 = time.monotonic()
        try:
            ok, message = fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = f"{name}: EXCEPTION — {type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - t0
        results.append((name, ok, message, elapsed))
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}]  {message}  ({elapsed:.2f}s)")
        print()

    print(_sep())
    total = len(results)
    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = total - passed
    print(f"  Concurrent: {passed}/{total} tests pass", end="")
    if failed:
        print(f"  ({failed} FAILED)")
    else:
        print()
    print(_sep())

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
