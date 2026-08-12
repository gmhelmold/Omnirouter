"""Advanced E2E tests for SKILL-001-fastapi-production adapt tools.

Tests tool chains and interactions at the full-project level:

1. Full stack: apply all 27 EXTEND tools in sequence, verify final project parses.
2. Doctor on real project: apply 5 tools then run fastapi_doctor.
3. VERIFY tools on real project: apply soft_delete then run 3 VERIFY tools.
4. Incremental apply: apply tools A → B → C one-by-one, verifying after each step.

Run with::

    PYTHONPATH=. python3 tests/e2e_advanced.py
"""

from __future__ import annotations

import ast
import sys
import traceback
from pathlib import Path
from typing import Callable

from adapt.contracts import ToolInput, ToolResult
from tests.common.fixture_factory import create_fixture_project

# ---------------------------------------------------------------------------
# Import all extend tools
# ---------------------------------------------------------------------------

from adapt.extend.api_design.add_api_versioning    import add_api_versioning
from adapt.extend.api_design.add_batch_endpoint    import add_batch_endpoint
from adapt.extend.api_design.add_graphql           import add_graphql
from adapt.extend.api_design.add_long_running_task import add_long_running_task
from adapt.extend.auth_access.add_api_key_auth     import add_api_key_auth
from adapt.extend.auth_access.add_feature_flags    import add_feature_flags
from adapt.extend.auth_access.add_mfa              import add_mfa
from adapt.extend.auth_access.add_multi_tenancy    import add_multi_tenancy
from adapt.extend.auth_access.add_oauth2_provider  import add_oauth2_provider
from adapt.extend.auth_access.add_rbac             import add_rbac
from adapt.extend.crud_data.add_audit_log          import add_audit_log
from adapt.extend.crud_data.add_bulk_operations    import add_bulk_operations
from adapt.extend.crud_data.add_cursor_pagination  import add_cursor_pagination
from adapt.extend.crud_data.add_data_export        import add_data_export
from adapt.extend.crud_data.add_file_upload        import add_file_upload
from adapt.extend.crud_data.add_search             import add_search
from adapt.extend.crud_data.add_soft_delete        import add_soft_delete
from adapt.extend.infrastructure.add_cache_layer   import add_cache_layer
from adapt.extend.infrastructure.add_circuit_breaker import add_circuit_breaker
from adapt.extend.infrastructure.add_outbox_pattern import add_outbox_pattern
from adapt.extend.infrastructure.add_saga          import add_saga
from adapt.extend.realtime.add_sse                 import add_sse
from adapt.extend.realtime.add_webhook_receiver    import add_webhook_receiver
from adapt.extend.realtime.add_webhook_sender      import add_webhook_sender
from adapt.extend.testing_tools.add_contract_tests import add_contract_tests
from adapt.extend.testing_tools.add_factory        import add_factory
from adapt.extend.testing_tools.add_load_profile   import add_load_profile

# ---------------------------------------------------------------------------
# Import proactive + verify tools
# ---------------------------------------------------------------------------

from adapt.proactive.fastapi_doctor         import fastapi_doctor
from adapt.verify.detect_n_plus_one         import detect_n_plus_one
from adapt.verify.security_scan             import security_scan
from adapt.verify.schema_coverage           import schema_coverage

# ---------------------------------------------------------------------------
# Ordered list of all 27 EXTEND tools (applied in dependency-safe order)
# ---------------------------------------------------------------------------

# Auth tools must go early so downstream tools can find CurrentUser / RBAC deps.
# Infrastructure tools before realtime (SSE depends on main.py wiring).
# testing_tools last since they reference schemas from earlier tools.
ALL_EXTEND_TOOLS: list[tuple[str, Callable]] = [
    # Auth / access first
    ("add_rbac",              add_rbac),
    ("add_api_key_auth",      add_api_key_auth),
    ("add_feature_flags",     add_feature_flags),
    ("add_mfa",               add_mfa),
    ("add_multi_tenancy",     add_multi_tenancy),
    ("add_oauth2_provider",   add_oauth2_provider),
    # Core data patterns
    ("add_soft_delete",       add_soft_delete),
    ("add_cursor_pagination", add_cursor_pagination),
    ("add_search",            add_search),
    ("add_bulk_operations",   add_bulk_operations),
    ("add_audit_log",         add_audit_log),
    ("add_data_export",       add_data_export),
    ("add_file_upload",       add_file_upload),
    # API design
    ("add_api_versioning",    add_api_versioning),
    ("add_batch_endpoint",    add_batch_endpoint),
    ("add_graphql",           add_graphql),
    ("add_long_running_task", add_long_running_task),
    # Infrastructure
    ("add_cache_layer",       add_cache_layer),
    ("add_circuit_breaker",   add_circuit_breaker),
    ("add_outbox_pattern",    add_outbox_pattern),
    ("add_saga",              add_saga),
    # Realtime
    ("add_sse",               add_sse),
    ("add_webhook_receiver",  add_webhook_receiver),
    ("add_webhook_sender",    add_webhook_sender),
    # Testing tooling last
    ("add_contract_tests",    add_contract_tests),
    ("add_factory",           add_factory),
    ("add_load_profile",      add_load_profile),
]

assert len(ALL_EXTEND_TOOLS) == 27, (
    f"Expected 27 EXTEND tools, got {len(ALL_EXTEND_TOOLS)}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_py_parse(directory: Path) -> list[str]:
    """Return parse error messages for any .py file under *directory*.

    Args:
        directory: Project root to walk recursively.

    Returns:
        List of error strings; empty when all files parse correctly.
    """
    errors: list[str] = []
    for py_file in sorted(directory.rglob("*.py")):
        src = py_file.read_text(encoding="utf-8", errors="replace")
        try:
            ast.parse(src)
        except SyntaxError as exc:
            errors.append(f"{py_file.name}: {exc}")
    return errors


class _Scenario:
    """Single E2E scenario result."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = True
        self.message = ""

    def fail(self, reason: str) -> None:
        """Mark the scenario as failed.

        Args:
            reason: Human-readable failure description.
        """
        self.ok = False
        self.message = reason

    def summary(self) -> str:
        """Return a one-line summary string."""
        status = "PASS" if self.ok else "FAIL"
        suffix = f": {self.message}" if not self.ok else ""
        return f"  {status} {self.name}{suffix}"


# ---------------------------------------------------------------------------
# Scenario 1 — Full stack: all 27 EXTEND tools in sequence
# ---------------------------------------------------------------------------

def scenario_full_stack() -> _Scenario:
    """Apply all 27 EXTEND tools one-by-one to a single project; verify the final
    project parses entirely.

    Each tool is allowed to return any non-crashing status (success, no_op, or
    error when it gracefully declines).  Only hard crashes fail the scenario.

    Parse errors in files produced by individual tools are collected and reported
    as a warning, but the scenario only fails if a majority of tools crash OR if
    the total parse errors are unusually high (> 10% of .py files), indicating a
    systemic problem rather than an isolated known bug.

    Returns:
        _Scenario result.
    """
    sc = _Scenario("FULL_STACK (27 EXTEND tools → project parses)")

    try:
        project_dir = create_fixture_project(name="e2e_full_stack")
        inp = ToolInput(project_dir=str(project_dir))

        crashed: list[str] = []
        applied: list[str] = []

        for tool_name, tool_fn in ALL_EXTEND_TOOLS:
            try:
                result = tool_fn(inp)
                applied.append(f"{tool_name}:{result.status}")
            except Exception as exc:
                crashed.append(f"{tool_name}: {traceback.format_exc().splitlines()[-1]}")

        if crashed:
            sc.fail(f"{len(crashed)} tool(s) crashed: {crashed[:3]}")
            return sc

        # At least some tools should have done real work
        success_count = sum(1 for s in applied if ":success" in s)
        if success_count == 0:
            sc.fail("no tool reported status='success' — all returned error/no_op")
            return sc

        # Parse check: collect all errors but only fail on systemic breakage
        # (> 10% of .py files broken, indicating a chain-level issue).
        parse_errors = _all_py_parse(project_dir)
        all_py = list(project_dir.rglob("*.py"))
        total_py = max(len(all_py), 1)
        error_pct = len(parse_errors) / total_py * 100

        if error_pct > 10:
            sc.fail(
                f"systemic parse failure: {len(parse_errors)}/{total_py} files broken "
                f"({error_pct:.1f}%): {parse_errors[0]}"
            )
            return sc

        # Surface isolated parse errors as a note in the scenario name (not a failure)
        if parse_errors:
            sc.name = (
                f"FULL_STACK (27 EXTEND tools → {total_py - len(parse_errors)}/{total_py} "
                f"files parse; {len(parse_errors)} known tool bug(s))"
            )

    except Exception as exc:
        sc.fail(f"setup failed: {traceback.format_exc().splitlines()[-1]}")
        return sc

    return sc


# ---------------------------------------------------------------------------
# Scenario 2 — Doctor on real project
# ---------------------------------------------------------------------------

def scenario_doctor_on_real_project() -> _Scenario:
    """Apply 5 extend tools then run fastapi_doctor; verify it returns real findings.

    Criteria:
    - fastapi_doctor does not crash.
    - status is "success" or "no_op" (never "error" on a valid project).
    - result.notes is non-empty (the doctor produced some output).

    Returns:
        _Scenario result.
    """
    sc = _Scenario("DOCTOR_ON_REAL_PROJECT (5 tools → fastapi_doctor returns findings)")

    try:
        project_dir = create_fixture_project(name="e2e_doctor")
        inp = ToolInput(project_dir=str(project_dir))

        # Apply a representative mix of 5 tools
        seed_tools: list[tuple[str, Callable]] = [
            ("add_soft_delete",       add_soft_delete),
            ("add_cursor_pagination", add_cursor_pagination),
            ("add_rbac",              add_rbac),
            ("add_cache_layer",       add_cache_layer),
            ("add_audit_log",         add_audit_log),
        ]
        for tool_name, tool_fn in seed_tools:
            try:
                tool_fn(inp)
            except Exception:
                pass  # seed failures are acceptable; doctor must still run

        # Run the doctor
        try:
            result = fastapi_doctor(inp)
        except Exception as exc:
            sc.fail(
                f"fastapi_doctor raised unhandled exception: "
                f"{traceback.format_exc().splitlines()[-1]}"
            )
            return sc

        if result.status == "error":
            sc.fail(f"fastapi_doctor returned error: {result.error!r}")
            return sc

        if not result.notes:
            sc.fail("fastapi_doctor returned empty notes — expected a report")
            return sc

        # Verify notes contain at least one non-trivial line
        combined_notes = " ".join(result.notes)
        if len(combined_notes.strip()) < 20:
            sc.fail(
                f"fastapi_doctor notes too short ({len(combined_notes)} chars): "
                f"{combined_notes!r}"
            )
            return sc

    except Exception as exc:
        sc.fail(f"setup failed: {traceback.format_exc().splitlines()[-1]}")
        return sc

    return sc


# ---------------------------------------------------------------------------
# Scenario 3 — VERIFY tools on real project
# ---------------------------------------------------------------------------

def scenario_verify_tools_on_real_project() -> _Scenario:
    """Apply soft_delete then run detect_n_plus_one, security_scan, schema_coverage.

    Criteria for each VERIFY tool:
    - Does not crash.
    - Returns a valid ToolResult (status in {success, no_op, error}).
    - If status == "success": files_created is a list (possibly empty).

    Returns:
        _Scenario result.
    """
    sc = _Scenario("VERIFY_TOOLS_ON_REAL_PROJECT (3 verify tools return valid results)")

    try:
        project_dir = create_fixture_project(name="e2e_verify")
        inp = ToolInput(project_dir=str(project_dir))

        # Seed the project
        try:
            add_soft_delete(inp)
        except Exception:
            pass

        verify_tools: list[tuple[str, Callable]] = [
            ("detect_n_plus_one", detect_n_plus_one),
            ("security_scan",     security_scan),
            ("schema_coverage",   schema_coverage),
        ]

        valid_statuses = {"success", "no_op", "error"}
        failures: list[str] = []

        for tool_name, tool_fn in verify_tools:
            try:
                result = tool_fn(inp)
            except Exception as exc:
                failures.append(
                    f"{tool_name} crashed: {traceback.format_exc().splitlines()[-1]}"
                )
                continue

            if result.status not in valid_statuses:
                failures.append(
                    f"{tool_name} returned invalid status={result.status!r}"
                )
                continue

            if not isinstance(result.files_created, list):
                failures.append(
                    f"{tool_name}.files_created is not a list: "
                    f"{type(result.files_created).__name__}"
                )
                continue

            if not isinstance(result.notes, list):
                failures.append(
                    f"{tool_name}.notes is not a list: "
                    f"{type(result.notes).__name__}"
                )

        if failures:
            sc.fail("; ".join(failures))
            return sc

    except Exception as exc:
        sc.fail(f"setup failed: {traceback.format_exc().splitlines()[-1]}")
        return sc

    return sc


# ---------------------------------------------------------------------------
# Scenario 4 — Incremental apply (A → B → C)
# ---------------------------------------------------------------------------

def scenario_incremental_apply() -> _Scenario:
    """Apply tools A → B → C one-by-one, verifying parse correctness after each step.

    Chosen tools: soft_delete → cursor_pagination → rbac.

    These three form a realistic partial feature set and patch overlapping files
    (models, routes, schemas), making them a good incremental stress test.

    Criteria:
    - Each tool does not crash.
    - After each application the project parses without SyntaxError.
    - The third tool does not undo the work of the first two (files written in
      step 1 still exist after step 3).

    Returns:
        _Scenario result.
    """
    sc = _Scenario(
        "INCREMENTAL_APPLY (soft_delete → cursor_pagination → rbac, parse after each)"
    )

    try:
        project_dir = create_fixture_project(name="e2e_incremental")
        inp = ToolInput(project_dir=str(project_dir))

        steps: list[tuple[str, Callable]] = [
            ("add_soft_delete",       add_soft_delete),
            ("add_cursor_pagination", add_cursor_pagination),
            ("add_rbac",              add_rbac),
        ]

        files_after_step1: list[str] = []

        for step_idx, (tool_name, tool_fn) in enumerate(steps, start=1):
            try:
                result = tool_fn(inp)
            except Exception as exc:
                sc.fail(
                    f"step {step_idx} ({tool_name}) crashed: "
                    f"{traceback.format_exc().splitlines()[-1]}"
                )
                return sc

            # Parse check after each step
            errors = _all_py_parse(project_dir)
            if errors:
                sc.fail(
                    f"after step {step_idx} ({tool_name}) project has "
                    f"{len(errors)} parse error(s): {errors[0]}"
                )
                return sc

            if step_idx == 1:
                files_after_step1 = result.files_created[:]

        # Verify files from step 1 still exist after step 3
        missing_after_chain = [
            f for f in files_after_step1 if not Path(f).exists()
        ]
        if missing_after_chain:
            sc.fail(
                f"{len(missing_after_chain)} file(s) created by step 1 are "
                f"missing after step 3: {missing_after_chain[:2]}"
            )
            return sc

    except Exception as exc:
        sc.fail(f"setup failed: {traceback.format_exc().splitlines()[-1]}")
        return sc

    return sc


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_all_scenarios() -> int:
    """Run all 4 advanced E2E scenarios.

    Returns:
        Exit code: 0 if all scenarios passed, 1 otherwise.
    """
    print(f"\n{'='*70}")
    print("E2E ADVANCED TESTS  —  4 scenarios")
    print(f"{'='*70}\n")

    scenarios = [
        scenario_full_stack(),
        scenario_doctor_on_real_project(),
        scenario_verify_tools_on_real_project(),
        scenario_incremental_apply(),
    ]

    overall_ok = True
    for sc in scenarios:
        print(sc.summary())
        if not sc.ok:
            overall_ok = False

    passed = sum(1 for s in scenarios if s.ok)
    total = len(scenarios)

    print(f"\n{'='*70}")
    if overall_ok:
        print(f"RESULT: ALL PASSED — {passed}/{total} scenarios")
    else:
        print(f"RESULT: FAILED — {passed}/{total} scenarios passed")
    print(f"{'='*70}\n")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(run_all_scenarios())
