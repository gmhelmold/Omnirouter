"""Property definitions 1-4 for property_tests.

Idempotency, dry-run purity, no-crash robustness, and parse correctness.
Split out of property_tests.py to stay under the 500-LOC cap. No behaviour
change — pure extraction.
"""

from __future__ import annotations

import random
import string
import traceback

from adapt.contracts import ToolInput, ToolResult
from tests.common.fixture_factory import create_fixture_project
from tests.property_tests__helpers import (
    _all_py_parse,
    _call_tool,
    _dir_sha256,
    _load_tool,
    _Results,
)

# ---------------------------------------------------------------------------
# Tool categorisation helpers
# ---------------------------------------------------------------------------

# Report/snapshot tools are designed to regenerate their output on every call
# (they produce fresh reports or snapshots rather than patching project structure).
# Idempotency is tested differently for them: they must not crash and must return
# a consistent status, but re-creating output files is expected behaviour.
_REPORT_TOOLS: frozenset[str] = frozenset(
    {
        "dead_code_finder",
        "dependency_graph",
        "sla_reporter",
        "error_rate_analyzer",
        "api_changelog",
        "blast_radius",
        "connection_pool_monitor",
        "migration_diff",
        "fastapi_doctor",
        "api_spec_compliance",
        "dependency_audit",
        "performance_baseline",
        "schema_coverage",
        "security_scan",
        "generate_sdk",  # regenerates openapi.json + .schema_hash every run
    }
)


# ---------------------------------------------------------------------------
# Property 1 — Idempotency
# ---------------------------------------------------------------------------


def prop_idempotency(tools: list[tuple[str, str, dict]], n_iter: int = 1) -> _Results:
    """Property: applying a write-capable tool twice must not re-create files on the
    second run.  Report/snapshot tools (those in _REPORT_TOOLS) are exempt from the
    "no re-creation" check — for them we only assert the tool does not crash.

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.
        n_iter: Iterations per tool (1 is sufficient for project-level idempotency).

    Returns:
        _Results accumulator.
    """
    results = _Results("IDEMPOTENCY", len(tools))

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        is_report_tool = fn_name in _REPORT_TOOLS

        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        try:
            project_dir = create_fixture_project(name=f"prop_idem_{fn_name[:20]}")
            inp = ToolInput(project_dir=str(project_dir))

            r1 = _call_tool(fn, inp, extra_kwargs)
            r2 = _call_tool(fn, inp, extra_kwargs)

            # Contract for report tools: must not crash on second run.
            if is_report_tool:
                # Any non-exception result is acceptable.
                results.record(label, True)
                continue

            # Contract for write/infrastructure tools: second run must not
            # re-create files that the first run already created.
            if r2.status == "error" and r1.status != "error":
                error_msg = r2.error or ""
                is_guard = any(
                    kw in error_msg.lower() for kw in ["already", "present", "exist", "no_op"]
                )
                if not is_guard:
                    results.record(label, False, f"second run returned error: {r2.error!r}")
                    continue

            if r1.status == "success" and r2.status == "success" and r2.files_created:
                results.record(
                    label,
                    False,
                    f"second run re-created {len(r2.files_created)} file(s): "
                    f"{r2.files_created[:2]}",
                )
                continue

            results.record(label, True)

        except Exception as exc:
            results.record(label, False, f"exception: {exc}")

    return results


# ---------------------------------------------------------------------------
# Property 2 — Dry-run purity
# ---------------------------------------------------------------------------


def prop_dry_run_purity(tools: list[tuple[str, str, dict]]) -> _Results:
    """Property: dry_run=True never modifies any file on disk.

    Takes a SHA-256 hash of the project directory before and after the dry-run
    call and asserts they are identical.

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.

    Returns:
        _Results accumulator.
    """
    results = _Results("DRY_RUN_PURITY", len(tools))

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        try:
            project_dir = create_fixture_project(name=f"prop_dry_{fn_name[:20]}")
            inp = ToolInput(project_dir=str(project_dir), dry_run=True)

            before = _dir_sha256(project_dir)
            _call_tool(fn, inp, extra_kwargs)
            after = _dir_sha256(project_dir)

            if before != after:
                results.record(label, False, "dry_run=True modified at least one file on disk")
            else:
                results.record(label, True)

        except Exception as exc:
            results.record(label, False, f"exception: {exc}")

    return results


# ---------------------------------------------------------------------------
# Property 3 — No crash on any path (robustness under arbitrary input)
# ---------------------------------------------------------------------------


def prop_error_on_invalid_dir(tools: list[tuple[str, str, dict]], n_iter: int = 5) -> _Results:
    """Property: calling any tool with a novel (never-used) temp path must never
    raise an unhandled exception or call sys.exit().

    Adapt tools are generators and are allowed to create a skeleton project at
    the given path (returning "success").  The contract is only that they do NOT
    crash — every exit path must go through ToolResult.

    Uses multiple distinct random /tmp/ paths to catch edge cases.

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.
        n_iter: Number of distinct paths to try per tool.

    Returns:
        _Results accumulator.
    """
    results = _Results("NO_CRASH_ON_ANY_PATH", len(tools))

    rng = random.Random(42)

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        tool_ok = True
        failure_msg = ""

        for i in range(n_iter):
            rand_suffix = "".join(rng.choices(string.ascii_lowercase, k=16))
            # Use /tmp/ so macOS allows directory creation.
            test_path = f"/tmp/skill001_robustness_{rand_suffix}_{i}/project"

            try:
                inp = ToolInput(project_dir=test_path)
                result = _call_tool(fn, inp, extra_kwargs)

                if not isinstance(result, ToolResult):
                    tool_ok = False
                    failure_msg = (
                        f"returned non-ToolResult for path {test_path!r}: {type(result).__name__}"
                    )
                    break

            except SystemExit as exc:
                tool_ok = False
                failure_msg = f"called sys.exit({exc.code}) for path {test_path!r}"
                break
            except Exception:
                tool_ok = False
                failure_msg = (
                    f"raised unhandled exception for path {test_path!r}: "
                    f"{traceback.format_exc().splitlines()[-1]}"
                )
                break

        results.record(label, tool_ok, failure_msg)

    return results


# ---------------------------------------------------------------------------
# Property 4 — Parse correctness
# ---------------------------------------------------------------------------


def prop_parse_correctness(tools: list[tuple[str, str, dict]]) -> _Results:
    """Property: after applying any tool, all .py files in the project parse with ast.parse.

    Skips tools that return status='error' (they may have legitimately found
    nothing to work on in the generated project).

    Args:
        tools: List of (module_path, fn_name, extra_kwargs) tuples.

    Returns:
        _Results accumulator.
    """
    results = _Results("PARSE_CORRECTNESS", len(tools))

    for module_path, fn_name, extra_kwargs in tools:
        label = f"{module_path}.{fn_name}"
        try:
            fn = _load_tool(module_path, fn_name)
        except Exception as exc:
            results.record(label, False, f"import error: {exc}")
            continue

        try:
            project_dir = create_fixture_project(name=f"prop_parse_{fn_name[:18]}")
            inp = ToolInput(project_dir=str(project_dir))

            result = _call_tool(fn, inp, extra_kwargs)

            if result.status == "error":
                # Not a property violation — tool gracefully declined.
                results.record(label, True)
                continue

            errors = _all_py_parse(project_dir)
            if errors:
                results.record(label, False, f"{len(errors)} parse error(s): {errors[0]}")
            else:
                results.record(label, True)

        except Exception as exc:
            results.record(label, False, f"exception: {exc}")

    return results
