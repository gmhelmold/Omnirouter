"""test_consistency__part3.py — Alembic migration + config consistency + runner.

Split out of tests/test_consistency.py (BRUTAL hardening round 4C) to keep
every sibling file under the 500-LOC cap. Carries Test 5 (Alembic migration
chain integrity), Test 6 (config/env-var coverage), and the standalone
``run_all_tests`` runner that exercises all 6 consistency checks.

Run standalone:
    PYTHONPATH=. python3 tests/test_consistency__part3.py
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
import traceback
from collections import defaultdict
from pathlib import Path

# Imported under non-``test_`` aliases so pytest does not re-collect them in
# this module (they are owned by part1/part2). They are only referenced by the
# standalone ``run_all_tests`` runner below via ``_TESTS``.
from tests.test_consistency__part1 import (  # noqa: E402
    check_1_no_import_cycles as _run_test_1,
)
from tests.test_consistency__part1 import (
    check_2_model_consistency as _run_test_2,
)
from tests.test_consistency__part2 import (  # noqa: E402
    check_3_schema_consistency as _run_test_3,
)
from tests.test_consistency__part2 import (
    check_4_route_consistency as _run_test_4,
)
from tests.test_consistency__shared import (  # noqa: E402
    _build_10_tool_project,
    _collect_py_files,
)

# ---------------------------------------------------------------------------
# Test 5 — Alembic migration chain consistency
# ---------------------------------------------------------------------------

# Matches both:  revision = "foo"  and  revision: str = "foo"
_REVISION_RE = re.compile(
    r'^revision\s*(?::\s*[^=]+?)?\s*=\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)
# Matches: down_revision = "foo" | down_revision = None | down_revision: ... = None
# ``[^=]+?`` tolerates multi-token annotations like ``Union[str, None]``
# (the prior ``\S+`` stopped at the first space inside the annotation).
_DOWN_REVISION_RE = re.compile(
    r'^down_revision\s*(?::\s*[^=]+?)?\s*=\s*(?:["\']([^"\']*)["\']|(None))',
    re.MULTILINE,
)


def _parse_migration(path: Path) -> dict | None:
    """Parse an Alembic migration file.

    Returns:
        Dict with revision, down_revision, has_upgrade, has_downgrade,
        downgrade_is_pass_only.  Returns None if parsing fails.
    """
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError:
        return None

    rev_match = _REVISION_RE.search(source)
    if rev_match is None:
        return None

    revision = rev_match.group(1)

    down_match = _DOWN_REVISION_RE.search(source)
    down_revision: str | None = None
    # group(1) = string value, group(2) = "None"
    # group(2) = literal None → keep down_revision as None
    if down_match and down_match.group(1) is not None:
        down_revision = down_match.group(1)

    has_upgrade = bool(re.search(r"^def upgrade\(", source, re.MULTILINE))
    has_downgrade = bool(re.search(r"^def downgrade\(", source, re.MULTILINE))

    # Detect pass-only downgrade: body contains only "pass" (ignoring comments/docstrings)
    downgrade_is_pass_only = _is_downgrade_pass_only(source)

    return {
        "revision": revision,
        "down_revision": down_revision,
        "has_upgrade": has_upgrade,
        "has_downgrade": has_downgrade,
        "downgrade_is_pass_only": downgrade_is_pass_only,
        "file": path,
    }


def _is_downgrade_pass_only(source: str) -> bool:
    """Return True if the downgrade() function has only a pass statement."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            body = node.body
            # Filter out docstrings
            stmts = [
                s
                for s in body
                if not (
                    isinstance(s, ast.Expr)
                    and isinstance(s.value, ast.Constant)
                    and isinstance(s.value.value, str)
                )
            ]
            return len(stmts) == 1 and isinstance(stmts[0], ast.Pass)
    return False


def check_5_migration_chain(project_dir: Path) -> tuple[bool, str]:
    """Test 5: Alembic migration chain integrity.

    Checks:
    - Every migration parses via ast.parse
    - Every migration has upgrade() and downgrade()
    - No downgrade() is pass-only
    - Revision chain is linear (no forks)

    Returns:
        (passed, message)
    """
    versions_dir = project_dir / "alembic" / "versions"
    if not versions_dir.exists():
        return False, "alembic/versions/ directory not found"

    migration_files = sorted(versions_dir.glob("*.py"))
    if not migration_files:
        return False, "No migration files found in alembic/versions/"

    failures: list[str] = []
    migrations: list[dict] = []

    for mf in migration_files:
        if mf.name == "__init__.py":
            continue
        info = _parse_migration(mf)
        if info is None:
            failures.append(
                f"{mf.name}: failed to parse — SyntaxError or missing 'revision' identifier"
            )
            continue
        migrations.append(info)

        if not info["has_upgrade"]:
            failures.append(f"{mf.name}: missing upgrade() function")

        if not info["has_downgrade"]:
            failures.append(f"{mf.name}: missing downgrade() function")

        if info["has_downgrade"] and info["downgrade_is_pass_only"]:
            # The no-op chain root (0001_initial, down_revision=None) is
            # intentionally empty — there is nothing to undo at the root.
            # Other migrations must still implement a real downgrade.
            # See R6-O4-A1.
            is_chain_root = (
                info.get("down_revision") is None and info.get("revision") == "0001_initial"
            )
            if not is_chain_root:
                failures.append(
                    f"{mf.name}: downgrade() body is pass-only — must contain actual undo logic"
                )

    # Check for fork: two migrations pointing to same down_revision
    down_rev_to_files: dict[str | None, list[str]] = defaultdict(list)
    for m in migrations:
        down_rev_to_files[m["down_revision"]].append(m["file"].name)

    for down_rev, files in down_rev_to_files.items():
        if down_rev is None:
            if len(files) > 1:
                failures.append(
                    f"Migration chain fork: multiple root migrations "
                    f"(down_revision=None): {', '.join(sorted(files))}"
                )
        elif len(files) > 1:
            failures.append(
                f"Migration chain fork at '{down_rev}': "
                f"{len(files)} migrations share the same parent — "
                f"chain is not linear: {', '.join(sorted(files))}"
            )

    if failures:
        detail = "\n  ".join(failures)
        return False, f"Migration chain failures ({len(failures)}):\n  {detail}"

    return True, (
        f"All {len(migrations)} migrations valid (upgrade+downgrade present, chain is linear)"
    )


# ---------------------------------------------------------------------------
# Test 6 — Config consistency
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r'\bos\.getenv\(["\']([A-Z_][A-Z0-9_]*)["\']')
_SETTINGS_CLASS_FIELD_RE = re.compile(r"^\s{4}([A-Z_][A-Z0-9_]*)\s*(?::\s|\s*=)", re.MULTILINE)
_ENV_EXAMPLE_KEY_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)
# Module-level bare assignments like SECRET_KEY: int = 15 (added by tools outside Settings)
_MODULE_LEVEL_VAR_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*(?::\s*\S+\s*)?=", re.MULTILINE)


def check_6_config_consistency(project_dir: Path) -> tuple[bool, str]:
    """Test 6: Config/env-var coverage.

    Checks:
    - app/core/config.py exists and parses cleanly
    - All os.getenv() calls across app/ reference vars defined in
      Settings fields OR .env.example OR module-level config vars
    - SECRET_KEY has a warning/validation for the default value

    Returns:
        (passed, message)
    """
    failures: list[str] = []
    config_file = project_dir / "app" / "core" / "config.py"

    # (a) config.py must exist and parse
    if not config_file.exists():
        return False, "app/core/config.py not found"

    config_source = config_file.read_text(encoding="utf-8")
    try:
        ast.parse(config_source)
    except SyntaxError as exc:
        return False, f"app/core/config.py has SyntaxError: {exc}"

    # (b) Collect known variable names from config.py
    #     - Settings class fields
    #     - Module-level uppercase vars (added by tools like add_sse)
    settings_fields: set[str] = set(_SETTINGS_CLASS_FIELD_RE.findall(config_source))
    module_level_vars: set[str] = set(_MODULE_LEVEL_VAR_RE.findall(config_source))

    # (c) .env.example keys
    env_example_file = project_dir / ".env.example"
    env_example_keys: set[str] = set()
    if env_example_file.exists():
        env_example_keys = set(_ENV_EXAMPLE_KEY_RE.findall(env_example_file.read_text()))

    known_vars = settings_fields | env_example_keys | module_level_vars

    # (d) Scan all app/ Python files for os.getenv() calls
    app_py_files = _collect_py_files(project_dir / "app", exclude_dirs={"__pycache__"})
    undefined_refs: list[tuple[str, Path]] = []
    for py_file in app_py_files:
        source = py_file.read_text(encoding="utf-8")
        for match in _ENV_VAR_RE.finditer(source):
            var_name = match.group(1)
            if var_name not in known_vars:
                undefined_refs.append((var_name, py_file))

    if undefined_refs:
        seen_vars: set[str] = set()
        for var_name, py_file in undefined_refs:
            if var_name in seen_vars:
                continue
            seen_vars.add(var_name)
            rel = py_file.relative_to(project_dir)
            failures.append(
                f"os.getenv('{var_name}') in {rel}: "
                "not defined in Settings, module-level config, or .env.example"
            )

    # (e) SECRET_KEY must have default-value warning or validation
    has_secret_key_guard = "SECRET_KEY" in config_source and (
        "changethis" in config_source
        or "warnings.warn" in config_source
        or "raise ValueError" in config_source
    )
    if not has_secret_key_guard:
        failures.append("app/core/config.py: SECRET_KEY has no default-value warning or validation")

    if failures:
        detail = "\n  ".join(failures)
        return False, f"Config consistency failures ({len(failures)}):\n  {detail}"

    return True, (
        f"Config consistent: {len(settings_fields)} Settings fields, "
        f"{len(env_example_keys)} .env.example keys, "
        f"SECRET_KEY guarded"
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

_TESTS = [
    ("Import cycle detection", _run_test_1),
    ("Model consistency", _run_test_2),
    ("Schema consistency", _run_test_3),
    ("Route consistency", _run_test_4),
    ("Migration chain", check_5_migration_chain),
    ("Config consistency", check_6_config_consistency),
]


def run_all_tests() -> int:
    """Build the 10-tool project once, then run all 6 consistency tests.

    Returns:
        Number of tests that passed.
    """
    print("=" * 70)
    print("CONSISTENCY TESTS — 10-tool FastAPI project")
    print("=" * 70)

    print("\n[SETUP] Generating base project + applying 10 tools...")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            project_dir = _build_10_tool_project(tmp_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[FATAL] Could not build project: {exc}")
            traceback.print_exc()
            return 0

        print(f"[SETUP] Project at: {project_dir}")
        print()

        passed = 0
        for i, (name, fn) in enumerate(_TESTS, 1):
            print(f"[TEST {i}/6] {name}")
            try:
                ok, msg = fn(project_dir)
            except Exception as exc:  # noqa: BLE001
                ok = False
                msg = f"EXCEPTION: {exc}"
                traceback.print_exc()

            status = "PASS" if ok else "FAIL"
            indented_msg = msg.replace("\n", "\n  ")
            print(f"  [{status}] {indented_msg}")
            if ok:
                passed += 1
            print()

        print("=" * 70)
        print(f"RESULT: {passed}/6 tests passed")
        print("=" * 70)

    return passed


if __name__ == "__main__":
    passed = run_all_tests()
    print(f"\nConsistency: {passed}/6 tests pass")
    sys.exit(0 if passed == 6 else 1)
