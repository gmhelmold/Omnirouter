"""Edge case tests for SKILL-001 blind spots.

Blind Spot A: Edge case model names — multi-word, single-letter, many models,
              large models, numeric names, underscore fields, complex field types.

Blind Spot B: Alembic migration chain validity — add_soft_delete + add_audit_log
              migrations parse, have upgrade/downgrade, form a non-gapped chain,
              and have non-empty downgrade bodies.

Run:
    PYTHONPATH=. python3 tests/test_edge_cases.py
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from adapt.contracts import ToolInput
from adapt.extend.crud_data.add_audit_log import add_audit_log
from adapt.extend.crud_data.add_soft_delete import add_soft_delete
from generators.orchestrator import generate_project

# ---------------------------------------------------------------------------
# Python executable resolution
# ---------------------------------------------------------------------------


def _resolve_python() -> str:
    """Return a Python executable that has FastAPI installed.

    Prefers the ``.venv`` interpreter co-located with this skill (which
    has all generated-project dependencies installed).  Falls back to
    ``sys.executable`` so tests still run in environments where the current
    interpreter already has FastAPI.

    Returns:
        Absolute path string to the Python executable to use for boot tests.
    """
    skill_root = Path(__file__).resolve().parent.parent
    venv_python = skill_root / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


_PYTHON = _resolve_python()


def _boot_timeout() -> float:
    """Boot subprocess timeout (seconds), tunable for loaded CI runners.

    Defaults to 60s — generous enough to survive CPU contention on a
    loaded CI runner — and overridable via ``HUGR_BOOT_TIMEOUT``.
    Falls back to the default if the env var is unset or unparseable.
    """
    raw = os.environ.get("HUGR_BOOT_TIMEOUT")
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 60.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _boot_project(project_dir: Path) -> tuple[bool, str]:
    """Attempt to import ``app.main`` in a subprocess.

    Args:
        project_dir: Root directory of the generated FastAPI project.

    Returns:
        Tuple of (success: bool, detail: str).
        detail is 'BOOT_OK' on success, or an error excerpt on failure.
    """
    result = subprocess.run(
        [
            _PYTHON,
            "-c",
            "import sys; sys.path.insert(0, '.'); from app.main import app; print('BOOT_OK')",
        ],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        timeout=_boot_timeout(),
    )
    if "BOOT_OK" in result.stdout:
        return True, "BOOT_OK"

    # Extract the last meaningful error line (most specific error is usually last)
    error_lines: list[str] = []
    for line in result.stderr.splitlines():
        stripped = line.strip()
        if stripped and any(
            kw in stripped for kw in ("Error", "error", "Exception", "Traceback", "SyntaxError")
        ):
            if any(
                skip in stripped for skip in ("pydantic", "opentelemetry", "logfire", "UserWarning")
            ):
                continue
            error_lines.append(stripped)

    if error_lines:
        # Return the last error line — typically the most specific exception message
        return False, error_lines[-1]
    return False, f"returncode={result.returncode} (no error line)"


def _assert_all_py_parse(project_root: Path) -> tuple[bool, str]:
    """Return (ok, error_msg) after ast.parse on every .py file.

    Args:
        project_root: Root directory to walk recursively.

    Returns:
        (True, '') if all files parse cleanly, (False, message) otherwise.
    """
    for py_file in sorted(project_root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return False, f"{py_file.relative_to(project_root)}: {exc}"
    return True, ""


def _generate_base(
    tmp_dir: Path,
    name: str,
    models: dict[str, dict[str, str]],
) -> Path:
    """Generate a project with given models.

    Args:
        tmp_dir: Temporary parent directory.
        name: Project / app name.
        models: Domain models dict passed to generate_project.

    Returns:
        Path to the generated project root.
    """
    out = tmp_dir / name
    owner_models = {m: "user" for m in models if m != "User"}
    generate_project(
        output_dir=str(out),
        name=name,
        models=models,
        owner_models=owner_models,
        with_auth=True,
        with_docker_compose=False,
        with_ci=False,
        with_otel=False,
        with_prometheus=False,
    )
    return out


# ---------------------------------------------------------------------------
# Blind Spot A — Edge case model names
# ---------------------------------------------------------------------------

# Each scenario: (label, models_dict)
EDGE_CASE_SCENARIOS: list[tuple[str, dict[str, dict[str, str]]]] = [
    # 1. Multi-word PascalCase names
    (
        "multi_word_names",
        {
            "OrderItem": {"quantity": "int", "price": "Decimal"},
            "PaymentMethod": {"type": "str", "last_four": "str"},
            "ShippingAddress": {"street": "str", "city": "str", "country": "str"},
        },
    ),
    # 2. Single-letter names
    (
        "single_letter_names",
        {
            "X": {"value": "str"},
            "A": {"code": "int"},
        },
    ),
    # 3. Many models at once (10 models)
    (
        "many_models_10",
        {
            "Alpha": {"name": "str"},
            "Beta": {"name": "str"},
            "Gamma": {"name": "str"},
            "Delta": {"name": "str"},
            "Epsilon": {"name": "str"},
            "Zeta": {"name": "str"},
            "Eta": {"name": "str"},
            "Theta": {"name": "str"},
            "Iota": {"name": "str"},
            "Kappa": {"name": "str"},
        },
    ),
    # 4. One model with 20 fields
    (
        "fat_model_20_fields",
        {
            "Profile": {
                "first_name": "str",
                "last_name": "str",
                "age": "int",
                "bio": "text",
                "score": "float",
                "balance": "Decimal",
                "birthday": "date",
                "last_login": "datetime",
                "contact_email": "email",
                "website": "url",
                "is_verified": "bool",
                "is_premium": "bool",
                "phone": "str",
                "country": "str",
                "city": "str",
                "zip_code": "str",
                "avatar_url": "str",
                "referral_code": "str",
                "points": "int",
                "level": "int",
            },
        },
    ),
    # 5. Names with numbers (digits in name)
    (
        "names_with_numbers",
        {
            "Item2": {"value": "str"},
            "V2Order": {"status": "str", "total": "Decimal"},
        },
    ),
    # 6. Fields with underscores (snake_case field names)
    (
        "underscore_fields",
        {
            "Employee": {
                "first_name": "str",
                "last_name": "str",
                "is_active": "bool",
                "hire_date": "date",
                "job_title": "str",
            },
        },
    ),
    # 7a. Complex field types — safe json field names (no SQLAlchemy reserved names)
    (
        "complex_field_types_safe",
        {
            "Config": {
                "extra_data": "json",  # 'metadata' is RESERVED in SQLAlchemy Base — must use safe name
                "tags": "json",
                "settings": "json",
                "name": "str",
                "version": "str",
            },
        },
    ),
    # 7b. Complex field types — reserved name 'metadata' triggers SQLAlchemy error
    #     EXPECTED TO FAIL: sqlalchemy.exc.InvalidRequestError:
    #     "Attribute name 'metadata' is reserved when using the Declarative API."
    #     This documents a KNOWN BUG: the model generator does not guard against
    #     SQLAlchemy-reserved attribute names (DeclarativeBase.metadata, .registry).
    (
        "complex_field_types_reserved_metadata",
        {
            "Config": {
                "metadata": "json",  # BUG: reserved name — crashes at import time
                "name": "str",
            },
        },
    ),
]

# Scenarios expected to fail due to known bugs (marked as KNOWN_FAILING).
# The test runner records these as "KNOWN FAIL" rather than "FAIL".
KNOWN_FAILING: set[str] = {
    "complex_field_types_reserved_metadata",  # SQLAlchemy reserved name 'metadata'
}


def run_blind_spot_a() -> tuple[int, int, list[str], list[str]]:
    """Run all edge-case model scenarios.

    Scenarios in KNOWN_FAILING are expected to fail; they are tracked
    separately and do not count against the pass/total ratio.

    Returns:
        (passed_count, total_unexpectedly_failing, failure_messages, known_fail_messages)
        where total in the pass ratio = number of non-known-failing scenarios.
    """
    passed = 0
    failures: list[str] = []
    known_fails: list[str] = []

    non_known = [(l, m) for l, m in EDGE_CASE_SCENARIOS if l not in KNOWN_FAILING]
    total = len(non_known)

    all_scenarios = EDGE_CASE_SCENARIOS  # iterate all; route known-failing separately

    for label, models in all_scenarios:
        is_known_failing = label in KNOWN_FAILING
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            try:
                project_dir = _generate_base(tmp_path, label, models)
            except Exception as exc:
                msg = f"{label}: GENERATE FAILED — {exc}"
                if is_known_failing:
                    known_fails.append(msg)
                else:
                    failures.append(msg)
                continue

            # 1. All .py files must parse
            ok, parse_err = _assert_all_py_parse(project_dir)
            if not ok:
                msg = f"{label}: SYNTAX ERROR — {parse_err}"
                if is_known_failing:
                    known_fails.append(msg)
                else:
                    failures.append(msg)
                continue

            # 2. Boot test
            boot_ok, detail = _boot_project(project_dir)
            if boot_ok:
                if not is_known_failing:
                    passed += 1
                else:
                    # Known-failing scenario unexpectedly passed — that's a fix!
                    known_fails.append(f"{label}: UNEXPECTEDLY PASSED (bug may be fixed)")
            else:
                msg = f"{label}: BOOT FAILED — {detail}"
                if is_known_failing:
                    known_fails.append(msg)
                else:
                    failures.append(msg)

    return passed, total, failures, known_fails


# ---------------------------------------------------------------------------
# Blind Spot B — Alembic migration chain validity
# ---------------------------------------------------------------------------


def _collect_migration_meta(versions_dir: Path) -> dict[str, dict]:
    """Parse every migration file and extract revision metadata.

    Args:
        versions_dir: Path to ``alembic/versions/`` directory.

    Returns:
        Dict of {revision_id: {
            'file': Path,
            'down_revision': str | None,
            'has_upgrade': bool,
            'has_downgrade': bool,
            'downgrade_empty': bool,   # True if body is only `pass`
            'parse_ok': bool,
            'parse_error': str,
        }}
    """
    meta: dict[str, dict] = {}
    for f in sorted(versions_dir.glob("*.py")):
        source = f.read_text(encoding="utf-8")
        record: dict = {
            "file": f,
            "down_revision": None,
            "has_upgrade": False,
            "has_downgrade": False,
            "downgrade_empty": False,
            "parse_ok": False,
            "parse_error": "",
        }
        try:
            tree = ast.parse(source)
            record["parse_ok"] = True
        except SyntaxError as exc:
            record["parse_error"] = str(exc)
            # Use filename stem as key when we can't extract revision
            meta[f.stem] = record
            continue

        # Walk AST to extract module-level assignments and function defs
        revision_id = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "revision":
                            if isinstance(node.value, ast.Constant):
                                revision_id = node.value.value
                        elif target.id == "down_revision":
                            if isinstance(node.value, ast.Constant):
                                record["down_revision"] = node.value.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if (
                    node.target.id == "revision"
                    and node.value
                    and isinstance(node.value, ast.Constant)
                ):
                    revision_id = node.value.value
                elif (
                    node.target.id == "down_revision"
                    and node.value
                    and isinstance(node.value, ast.Constant)
                ):
                    record["down_revision"] = node.value.value
            elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                fn_name = node.name
                if fn_name == "upgrade":
                    record["has_upgrade"] = True
                elif fn_name == "downgrade":
                    record["has_downgrade"] = True
                    # Check if body is just `pass`
                    body_stmts = node.body
                    # Filter out docstrings
                    non_doc = [
                        s
                        for s in body_stmts
                        if not (
                            isinstance(s, ast.Expr)
                            and isinstance(s.value, ast.Constant)
                            and isinstance(s.value.value, str)
                        )
                    ]
                    if len(non_doc) == 1 and isinstance(non_doc[0], ast.Pass):
                        record["downgrade_empty"] = True

        key = revision_id if revision_id else f.stem
        meta[key] = record

    return meta


def _validate_migration_chain(meta: dict[str, dict]) -> list[str]:
    """Verify the migration chain has no gaps and no duplicate down_revisions.

    Args:
        meta: Output of _collect_migration_meta.

    Returns:
        List of error messages (empty = chain is valid).
    """
    errors: list[str] = []

    # Collect all revision IDs and down_revisions
    all_revisions = set(meta.keys())
    down_revisions = {v["down_revision"] for v in meta.values()}

    # Find the root migration (down_revision is None)
    roots = [r for r, v in meta.items() if v["down_revision"] is None]
    if len(roots) == 0:
        errors.append("CHAIN: No root migration found (no revision with down_revision=None)")
    elif len(roots) > 1:
        errors.append(f"CHAIN: Multiple root migrations found: {roots}")

    # Check that every non-None down_revision points to an existing revision
    for rev_id, info in meta.items():
        dr = info["down_revision"]
        if dr is not None and dr not in all_revisions:
            errors.append(
                f"CHAIN GAP: revision '{rev_id}' references down_revision='{dr}' "
                f"which does not exist in versions/"
            )

    # Check for duplicate down_revision values (excluding None — multiple roots handled above)
    non_null_dr = [v["down_revision"] for v in meta.values() if v["down_revision"] is not None]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for dr in non_null_dr:
        if dr in seen:
            duplicates.add(dr)
        seen.add(dr)
    if duplicates:
        errors.append(f"CHAIN: Duplicate down_revision values (branching detected): {duplicates}")

    return errors


def run_blind_spot_b() -> tuple[bool, str]:
    """Run Alembic migration validity checks.

    Generates a base project, applies add_soft_delete + add_audit_log,
    then validates all migration files.

    Returns:
        (all_ok: bool, detail_message: str)
    """
    issues: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        project_dir = _generate_base(
            tmp_path,
            "migration_test",
            {"Widget": {"name": "str", "price": "Decimal"}},
        )

        versions_dir = project_dir / "alembic" / "versions"
        if not versions_dir.exists():
            return False, "alembic/versions/ directory not created by generate_project"

        # Apply both tools
        inp = ToolInput(project_dir=str(project_dir))
        sd_result = add_soft_delete(inp)
        if sd_result.status == "error":
            return False, f"add_soft_delete failed: {sd_result.error}"

        al_result = add_audit_log(inp)
        if al_result.status == "error":
            return False, f"add_audit_log failed: {al_result.error}"

        # Collect and validate all migration files
        meta = _collect_migration_meta(versions_dir)

        if not meta:
            return False, "No migration files found in alembic/versions/"

        # ---- Check 1: All files parse ----
        for rev_id, info in meta.items():
            if not info["parse_ok"]:
                issues.append(f"PARSE ERROR in '{info['file'].name}': {info['parse_error']}")

        # ---- Check 2: Every migration has upgrade() and downgrade() ----
        for rev_id, info in meta.items():
            if not info["parse_ok"]:
                continue
            if not info["has_upgrade"]:
                issues.append(f"MISSING upgrade() in '{info['file'].name}' (rev={rev_id})")
            if not info["has_downgrade"]:
                issues.append(f"MISSING downgrade() in '{info['file'].name}' (rev={rev_id})")

        # ---- Check 3: downgrade() must not be just `pass` ----
        # Exception: the no-op chain root (0001_initial, down_revision=None)
        # is intentionally empty because nothing precedes it.  See R6-O4-A1.
        for rev_id, info in meta.items():
            if not info["parse_ok"]:
                continue
            if info["has_downgrade"] and info["downgrade_empty"]:
                is_chain_root = info.get("down_revision") is None and rev_id == "0001_initial"
                if not is_chain_root:
                    issues.append(
                        f"EMPTY downgrade() (only `pass`) in '{info['file'].name}' (rev={rev_id})"
                    )

        # ---- Check 4: Chain validity (no gaps, no duplicates) ----
        chain_errors = _validate_migration_chain(meta)
        issues.extend(chain_errors)

        # Build summary
        n = len(meta)
        revisions_list = sorted(meta.keys())
        summary_parts = [
            f"{n} migration(s) found: {revisions_list}",
        ]
        for rev_id, info in meta.items():
            dr = info["down_revision"]
            summary_parts.append(
                f"  [{rev_id}] -> down_revision={dr!r}, "
                f"upgrade={'OK' if info['has_upgrade'] else 'MISSING'}, "
                f"downgrade={'OK' if info['has_downgrade'] else 'MISSING'}"
                + (" [EMPTY!]" if info["downgrade_empty"] else "")
            )

        detail = "\n".join(summary_parts)

        if issues:
            detail = detail + "\nISSUES:\n" + "\n".join(f"  - {i}" for i in issues)
            return False, detail

        return True, detail


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run Blind Spot A and Blind Spot B tests, print summary.

    Returns:
        0 if all checks pass, 1 if any fail.
    """
    overall_ok = True

    # ---- Blind Spot A ----
    print("=" * 70)
    print("BLIND SPOT A: Edge case model names")
    print("=" * 70)
    a_passed, a_total, a_failures, a_known = run_blind_spot_a()
    for label, models in EDGE_CASE_SCENARIOS:
        is_known = label in KNOWN_FAILING
        in_failures = any(f.startswith(f"{label}:") for f in a_failures)
        in_known = any(f.startswith(f"{label}:") for f in a_known)

        if is_known:
            known_msg = next((f for f in a_known if f.startswith(f"{label}:")), "")
            detail = known_msg.split(" — ", 1)[-1] if " — " in known_msg else ""
            print(f"  KNOWN  {label} — {detail}")
        elif in_failures:
            fail_msg = next((f for f in a_failures if f.startswith(f"{label}:")), "")
            detail = fail_msg.split(" — ", 1)[-1] if " — " in fail_msg else fail_msg
            print(f"  FAIL   {label} — {detail}")
        else:
            print(f"  PASS   {label}")

    print(f"\nEdge case models: {a_passed}/{a_total} scenarios boot")
    if a_known:
        print(f"Known failing ({len(a_known)} scenario(s) with documented bugs):")
        for kf in a_known:
            print(f"  KNOWN  {kf}")
    if a_failures:
        overall_ok = False

    # ---- Blind Spot B ----
    print()
    print("=" * 70)
    print("BLIND SPOT B: Alembic migration chain validity")
    print("=" * 70)
    b_ok, b_detail = run_blind_spot_b()
    for line in b_detail.splitlines():
        print(f"  {line}")
    if b_ok:
        print("\nAlembic migrations: PASS — chain valid, all functions present, no empty downgrade")
    else:
        print("\nAlembic migrations: FAIL")
        overall_ok = False

    # ---- Final summary ----
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    known_note = f" (+{len(a_known)} known-failing)" if a_known else ""
    print(f"Edge case models: {a_passed}/{a_total} scenarios boot{known_note}")
    b_status = "PASS" if b_ok else "FAIL"
    b_issues = [line for line in b_detail.splitlines() if line.strip().startswith("-")]
    b_brief = f"{b_status} — {b_issues[0].strip() if b_issues else b_detail.splitlines()[0] if b_detail else 'ok'}"
    print(f"Alembic migrations: {b_brief}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
