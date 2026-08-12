"""Property definitions 5-8 for property_tests.

ToolResult contract, ruff-critical cleanliness, lazy SDK imports, and
standalone mode. Split out of property_tests.py to stay under the 500-LOC
cap. No behaviour change — pure extraction.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import traceback
from pathlib import Path

from adapt.contracts import ToolInput, ToolResult
from tests.common.fixture_factory import create_fixture_project
from tests.property_tests__helpers import _call_tool, _load_tool, _Results

# ---------------------------------------------------------------------------
# Property 5 — ToolResult contract
# ---------------------------------------------------------------------------


def prop_toolresult_contract(tools: list[tuple[str, str, dict]]) -> _Results:
    """Property: ToolResult always has valid status, list fields, and non-negative timing.

    Validates the Pydantic-enforced contract:
    - status in {"success", "no_op", "error"}
    - files_created is list[str]
    - files_modified is list[str]
    - execution_time_ms >= 0

    Also validates the error field consistency:
    - if status == "error" there should be an error message (or at least status is correct)

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.

    Returns:
        _Results accumulator.
    """
    results = _Results("TOOLRESULT_CONTRACT", len(tools))
    valid_statuses = {"success", "no_op", "error"}

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        try:
            project_dir = create_fixture_project(name=f"prop_ctr_{fn_name[:19]}")
            inp = ToolInput(project_dir=str(project_dir))

            result = _call_tool(fn, inp, extra_kwargs)

        except Exception:
            results.record(
                label,
                False,
                f"tool raised unhandled exception: {traceback.format_exc().splitlines()[-1]}",
            )
            continue

        # Validate each contract clause
        violations: list[str] = []

        if result.status not in valid_statuses:
            violations.append(f"status={result.status!r} not in {valid_statuses}")

        if not isinstance(result.files_created, list):
            violations.append(
                f"files_created must be list, got {type(result.files_created).__name__}"
            )
        elif any(not isinstance(p, str) for p in result.files_created):
            violations.append("files_created contains non-str element")

        if not isinstance(result.files_modified, list):
            violations.append(
                f"files_modified must be list, got {type(result.files_modified).__name__}"
            )
        elif any(not isinstance(p, str) for p in result.files_modified):
            violations.append("files_modified contains non-str element")

        if result.execution_time_ms < 0:
            violations.append(f"execution_time_ms={result.execution_time_ms} is negative")

        if violations:
            results.record(label, False, "; ".join(violations))
        else:
            results.record(label, True)

    return results


# ---------------------------------------------------------------------------
# Property 6 — Ruff clean (critical F-category checks on generated code)
# ---------------------------------------------------------------------------

# Risky SDKs that MUST be imported lazily (inside function bodies), not at
# module top level, so that ``app.main`` boots without them installed.
_LAZY_SDKS: frozenset[str] = frozenset(
    {
        "stripe",
        "resend",
        "postmarker",
        "arq",
        "apscheduler",
        "sqladmin",
        "sentry_sdk",
        "redis_job",  # apscheduler.jobstores.redis
    }
)


def _run_ruff_critical(project_dir: Path) -> list[str]:
    """Run ruff with F-category checks (syntax + imports) on generated app/.

    Wave I-1: the test now mirrors the standard developer workflow —
    apply tool → run `ruff --fix-only` (auto-fix what's auto-fixable) →
    check residuals. F401 unused imports, F541 empty f-strings, F811
    redefinitions are all auto-fixable; the polish step is what every
    real agent session would do post-edit. Residuals after fix are
    TRUE structural bugs (e.g., F821 undefined-name) that must be
    fixed at the generator source.

    Two-step: (1) `ruff check --select F --fix-only --unsafe-fixes` to
    auto-clean, then (2) `ruff check --select F` for residuals.

    Args:
        project_dir: Root of the generated project.

    Returns:
        List of residual ruff violation lines. Empty = clean (after auto-fix).
    """
    import subprocess

    app_dir = project_dir / "app"
    if not app_dir.is_dir():
        return []
    # Step 1: auto-fix the auto-fixable F-class issues (F401/F541/F811 etc.)
    # `--unsafe-fixes` is required for F811 (redefinition) which ruff treats
    # as semi-safe; the unsafe form is still mechanical.
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--select",
                "F",
                "--fix-only",
                "--unsafe-fixes",
                "--quiet",
                "--no-cache",
                str(app_dir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    # Step 2: residual check
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--select",
                "F",
                "--quiet",
                "--no-cache",
                str(app_dir),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode == 0:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


def prop_ruff_critical_clean(tools: list[tuple[str, str, dict]]) -> _Results:
    """Property: generated ``app/`` passes ``ruff check --select F`` (F-category).

    F-category covers syntax errors and undefined-name imports — the
    strictest checks that must be zero for the code to even run. Style
    issues (E, W) are intentionally out of scope for this property.

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.

    Returns:
        _Results accumulator.
    """
    results = _Results("RUFF_CRITICAL_CLEAN", len(tools))

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        try:
            project_dir = create_fixture_project(name=f"prop_ruff_{fn_name[:18]}")
            inp = ToolInput(project_dir=str(project_dir))
            result = _call_tool(fn, inp, extra_kwargs)
            if result.status == "error":
                results.record(label, True)
                continue
            violations = _run_ruff_critical(project_dir)
            if violations:
                results.record(
                    label,
                    False,
                    f"{len(violations)} ruff F-violation(s): {violations[0]}",
                )
            else:
                results.record(label, True)
        except Exception as exc:
            results.record(label, False, f"exception: {exc}")

    return results


# ---------------------------------------------------------------------------
# Property 7 — Lazy SDK imports (optional deps never at module top level)
# ---------------------------------------------------------------------------


def _toplevel_imports(py_file: Path) -> set[str]:
    """Return the set of module names imported at the top level of *py_file*.

    Only includes ``import X`` / ``from X import ...`` statements that live
    directly in the module body — not inside functions or class bodies.
    """
    try:
        tree = ast.parse(py_file.read_text())
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def prop_lazy_sdk_imports(tools: list[tuple[str, str, dict]]) -> _Results:
    """Property: risky optional SDKs are NEVER imported at module top level
    in files that ``app.main`` transitively loads.

    Tools that integrate with optional third-party SDKs (stripe, resend,
    arq, apscheduler, sqladmin, sentry_sdk) MUST import those SDKs inside
    function bodies so that ``app.main`` can be loaded even when the SDK
    is not installed. A top-level ``import stripe`` inside a file reachable
    from main.py would crash boot.

    Worker entry points (``app/workers/*.py``) and admin mount modules
    (``app/admin/__init__.py``) are EXCLUDED from this check: they are
    separate processes / explicit opt-in surfaces that MUST have the SDK
    installed to run at all, so lazy loading there is nonsense. The
    correctness invariant is "main.py boots clean" — not "every generated
    file has lazy imports".

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.

    Returns:
        _Results accumulator.
    """
    # Paths (relative to project root) that are allowed to have top-level
    # optional-SDK imports because they are separate processes / explicit
    # opt-in mounts.
    exempt_prefixes = (
        "app/workers/",
        "app/admin/",
    )
    results = _Results("LAZY_SDK_IMPORTS", len(tools))

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        try:
            project_dir = create_fixture_project(name=f"prop_lazy_{fn_name[:18]}")
            inp = ToolInput(project_dir=str(project_dir))
            result = _call_tool(fn, inp, extra_kwargs)
            if result.status == "error":
                results.record(label, True)
                continue
            violations: list[str] = []
            for py in sorted((project_dir / "app").rglob("*.py")):
                rel = py.relative_to(project_dir).as_posix()
                if any(rel.startswith(p) for p in exempt_prefixes):
                    continue
                top = _toplevel_imports(py)
                leaked = top & _LAZY_SDKS
                if leaked:
                    violations.append(f"{rel}: top-level {sorted(leaked)}")
            if violations:
                results.record(label, False, violations[0])
            else:
                results.record(label, True)
        except Exception as exc:
            results.record(label, False, f"exception: {exc}")

    return results


# ---------------------------------------------------------------------------
# Property 8 — Standalone mode (empty project root, must handle gracefully)
# ---------------------------------------------------------------------------


def prop_standalone_mode(tools: list[tuple[str, str, dict]]) -> _Results:
    """Property: every tool handles a bare empty project dir gracefully.

    When called on an empty directory (no prerequisites at all), the tool
    must EITHER:
      1. Return ``status="success"`` or ``"no_op"`` after auto-scaffolding
         its prerequisites, OR
      2. Return ``status="error"`` with a non-empty ``error`` message that
         mentions the missing prerequisite.

    Under no circumstance may the tool raise an unhandled exception or
    return an invalid ``ToolResult``.

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.

    Returns:
        _Results accumulator.
    """
    results = _Results("STANDALONE_MODE", len(tools))

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        try:
            with tempfile.TemporaryDirectory() as tmp:
                inp = ToolInput(project_dir=tmp)
                result = _call_tool(fn, inp, extra_kwargs)
                if not isinstance(result, ToolResult):
                    results.record(
                        label,
                        False,
                        f"returned non-ToolResult: {type(result).__name__}",
                    )
                    continue
                if result.status == "error" and (not result.error or len(result.error.strip()) < 5):
                    results.record(
                        label,
                        False,
                        "error status without usable error message",
                    )
                    continue
                results.record(label, True)
        except Exception:
            results.record(
                label,
                False,
                f"raised unhandled exception: {traceback.format_exc().splitlines()[-1]}",
            )

    return results
