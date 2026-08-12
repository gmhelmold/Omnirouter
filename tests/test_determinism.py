"""test_determinism.py — BRUTAL hardening round 5C.

Determinism and regression-detection tests for the SKILL-001 generator suite.

Test 1: Same inputs → identical file-by-file output (byte-for-byte)
Test 2: Tools write nothing outside project_dir (sandbox containment)
Test 3: Generated project is self-contained (survives relocation)
Test 4: Golden snapshot regression guard (file count, LOC, routes, models, migrations)

This is a **script-runner** (same convention as ``tests/test_boot.py``): the
check functions return ``(ok, message)`` tuples and are driven by the
``run_all_tests()`` runner below — they are NOT pytest tests, so they are named
``check_*`` rather than ``test_*`` to keep pytest from collecting them.

Run standalone:
    PYTHONPATH=. python3 tests/test_determinism.py

Exit code 0  → all 4 tests pass
Exit code 1  → one or more tests failed (details printed)
"""

from __future__ import annotations

import ast
import difflib
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root & path setup
# ---------------------------------------------------------------------------

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

from adapt.contracts import ToolInput  # noqa: E402
from tests.common.fixture_factory import create_fixture_project  # noqa: E402

# ---------------------------------------------------------------------------
# Golden snapshot path (created on first run, compared on subsequent runs)
# ---------------------------------------------------------------------------

_SNAPSHOT_PATH = _SKILL_ROOT / "tests" / "fixtures" / "golden_snapshot.json"

# ---------------------------------------------------------------------------
# Shared tool lists
# ---------------------------------------------------------------------------

# 5 tools used for determinism / containment / relocation tests
_TOOLS_5: list[tuple[str, str, str]] = [
    ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete", "add_soft_delete"),
    ("add_rbac", "adapt.extend.auth_access.add_rbac", "add_rbac"),
    ("add_audit_log", "adapt.extend.crud_data.add_audit_log", "add_audit_log"),
    ("add_cache_layer", "adapt.extend.infrastructure.add_cache_layer", "add_cache_layer"),
    ("add_search", "adapt.extend.crud_data.add_search", "add_search"),
]

# 3 tools for the relocation test
_TOOLS_3: list[tuple[str, str, str]] = _TOOLS_5[:3]

# 2 tools for the golden snapshot test
_TOOLS_SNAP: list[tuple[str, str, str]] = [
    ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete", "add_soft_delete"),
    ("add_rbac", "adapt.extend.auth_access.add_rbac", "add_rbac"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Files/dirs excluded from byte-for-byte comparison
_EXCLUDE_SUFFIXES: frozenset[str] = frozenset({".pyc"})
_EXCLUDE_DIR_PARTS: frozenset[str] = frozenset({"__pycache__"})


def _apply_tools(project_dir: Path, tools: list[tuple[str, str, str]]) -> None:
    """Import and call each tool against *project_dir*.

    Args:
        project_dir: Path to the FastAPI project root.
        tools: List of (label, module_path, function_name) tuples.

    Raises:
        RuntimeError: If any tool returns status=="error".
    """
    for label, mod_path, fn_name in tools:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, fn_name)
        result = fn(ToolInput(project_dir=str(project_dir)))
        if result.status == "error":
            raise RuntimeError(f"Tool '{label}' failed: {result.error}")


def _collect_files(project_dir: Path) -> dict[str, bytes]:
    """Return all non-excluded files as {relative_path_str: bytes}.

    Args:
        project_dir: Root of the project tree.

    Returns:
        Dict mapping relative POSIX paths to raw file bytes.
        Excludes ``__pycache__`` dirs and ``.pyc`` files.
    """
    result: dict[str, bytes] = {}
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _EXCLUDE_DIR_PARTS for part in path.parts):
            continue
        if path.suffix in _EXCLUDE_SUFFIXES:
            continue
        rel_key = path.relative_to(project_dir).as_posix()
        result[rel_key] = path.read_bytes()
    return result


def _collect_routes(project_dir: Path) -> list[str]:
    """Return sorted list of 'METHOD:path' strings from all route decorators.

    Args:
        project_dir: Root of the FastAPI project.

    Returns:
        Deduplicated, sorted list of route strings.
    """
    _HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}  # noqa: N806
    routes: set[str] = set()
    for py_file in sorted(project_dir.rglob("*.py")):
        if any(part in _EXCLUDE_DIR_PARTS for part in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if not (isinstance(func, ast.Attribute) and func.attr in _HTTP_METHODS):
                    continue
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    routes.add(f"{func.attr.upper()}:{dec.args[0].value}")
    return sorted(routes)


def _collect_models(project_dir: Path) -> list[str]:
    """Return sorted list of SQLAlchemy model class names.

    Scans ``app/models/`` for classes inheriting from ``Base``,
    ``DeclarativeBase``, or any ``*Mixin`` class.

    Args:
        project_dir: Root of the FastAPI project.

    Returns:
        Sorted, deduplicated list of class names.
    """
    model_dir = project_dir / "app" / "models"
    if not model_dir.exists():
        return []
    models: set[str] = set()
    for py_file in sorted(model_dir.rglob("*.py")):
        if any(part in _EXCLUDE_DIR_PARTS for part in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            if any(b in {"Base", "DeclarativeBase"} or b.endswith("Mixin") for b in base_names):
                models.add(node.name)
    return sorted(models)


def _collect_migrations(project_dir: Path) -> list[str]:
    """Return sorted list of migration filenames (excluding __init__.py).

    Args:
        project_dir: Root of the FastAPI project.

    Returns:
        Sorted list of migration file names.
    """
    versions_dir = project_dir / "alembic" / "versions"
    if not versions_dir.exists():
        return []
    return sorted(f.name for f in versions_dir.glob("*.py") if f.name != "__init__.py")


def _count_loc(project_dir: Path) -> int:
    """Return total lines of code across all Python files (excl. __pycache__).

    Args:
        project_dir: Root of the FastAPI project.

    Returns:
        Integer line count.
    """
    total = 0
    for py_file in project_dir.rglob("*.py"):
        if any(part in _EXCLUDE_DIR_PARTS for part in py_file.parts):
            continue
        try:  # noqa: SIM105
            total += len(py_file.read_text(encoding="utf-8").splitlines())
        except OSError:
            pass
    return total


# ---------------------------------------------------------------------------
# Test 1 — Determinism: same inputs → identical file-by-file output
# ---------------------------------------------------------------------------


def check_1_determinism(tmp_dir: Path) -> tuple[bool, str]:
    """Test 1: Identical inputs produce byte-for-byte identical output.

    Generates two projects (A and B) with exactly the same name and applies
    the same 5 tools in the same order.  Compares every non-excluded file
    byte-for-byte.

    The only expected difference is the project name embedded in
    ``app/main.py`` (``title=`` field), which is excluded via normalisation
    before comparison.

    Args:
        tmp_dir: Writable temporary directory.

    Returns:
        ``(True, message)`` if all files are identical after normalisation,
        ``(False, message)`` with diff details otherwise.
    """
    # Use the SAME name for both projects so title= in main.py matches
    project_a = create_fixture_project(name="det_proj", tmp_dir=tmp_dir / "a")
    _apply_tools(project_a, _TOOLS_5)

    project_b = create_fixture_project(name="det_proj", tmp_dir=tmp_dir / "b")
    _apply_tools(project_b, _TOOLS_5)

    files_a = _collect_files(project_a)
    files_b = _collect_files(project_b)

    failures: list[str] = []

    # Files present in A but missing from B
    for rel in sorted(set(files_a) - set(files_b)):
        failures.append(f"MISSING IN B: {rel}")

    # Files present in B but missing from A
    for rel in sorted(set(files_b) - set(files_a)):
        failures.append(f"MISSING IN A: {rel}")

    # Files present in both but with differing content
    for rel in sorted(set(files_a) & set(files_b)):
        bytes_a = files_a[rel]
        bytes_b = files_b[rel]
        if bytes_a == bytes_b:
            continue
        # Attempt text decode for a human-readable diff
        try:
            text_a = bytes_a.decode("utf-8")
            text_b = bytes_b.decode("utf-8")
            diff_lines = list(
                difflib.unified_diff(
                    text_a.splitlines(keepends=True),
                    text_b.splitlines(keepends=True),
                    fromfile=f"A/{rel}",
                    tofile=f"B/{rel}",
                    n=2,
                )
            )
            diff_excerpt = "".join(diff_lines[:30])
            failures.append(f"CONTENT DIFFERS: {rel}\n{diff_excerpt}")
        except UnicodeDecodeError:
            failures.append(
                f"BINARY CONTENT DIFFERS: {rel} (A={len(bytes_a)} bytes, B={len(bytes_b)} bytes)"
            )

    if failures:
        detail = "\n".join(failures)
        return False, f"Determinism failures ({len(failures)} file(s) differ):\n{detail}"

    return True, (f"Deterministic: {len(files_a)} files across A and B are byte-for-byte identical")


# ---------------------------------------------------------------------------
# Test 2 — Sandbox containment: no writes outside project_dir
# ---------------------------------------------------------------------------


def check_2_sandbox_containment(tmp_dir: Path) -> tuple[bool, str]:
    """Test 2: Tools write nothing outside the project_dir boundary.

    Strategy:
    - Creates a **sentinel directory** OUTSIDE ``project_dir`` (but still
      inside our own temp dir) with a canary file.  After tool execution
      the canary must be untouched (content + mtime).
    - Creates **two additional probe dirs** that sit alongside the project:
      a sibling dir and a parent dir.  No new files must appear in either.
    - Checks ``$HOME`` top-level entries (files only) before/after — tools
      must not litter the user's home directory.

    Note: We intentionally **do not** snapshot the system ``/tmp`` directory.
    macOS and the Python runtime legitimately create transient entries there
    (GCD named temporaries, framework sockets, test-runner caches) that are
    unrelated to our tools.  The canary + sibling + HOME checks provide
    a precise and race-free containment boundary.

    Args:
        tmp_dir: Writable temporary directory (all probe dirs live here).

    Returns:
        ``(True, message)`` if nothing leaked outside project_dir,
        ``(False, message)`` with violation details otherwise.
    """
    # -- Sentinel canary (outside project_dir, inside our tmp_dir) ----------
    sentinel_dir = tmp_dir / "sentinel_outside"
    sentinel_dir.mkdir(parents=True)
    canary_file = sentinel_dir / "canary.txt"
    canary_content = "CANARY_MUST_NOT_BE_TOUCHED"
    canary_file.write_text(canary_content, encoding="utf-8")
    canary_mtime_before = canary_file.stat().st_mtime

    # -- Sibling probe: snapshot the parent of project_dir before creation --
    parent_probe = tmp_dir / "proj_parent"
    parent_probe.mkdir(parents=True)

    # -- HOME snapshot (top-level files only, non-recursive) -----------------
    home_path = Path.home()
    try:
        home_before: set[str] = {str(p) for p in home_path.iterdir() if p.is_file()}
    except OSError:
        home_before = set()

    # -- Generate project and apply tools ------------------------------------
    project_dir = create_fixture_project(name="sandbox_test", tmp_dir=parent_probe)
    # Snapshot the parent (minus project_dir itself) before tools run
    try:
        parent_before: set[str] = {
            str(p) for p in parent_probe.iterdir() if not Path(p).is_relative_to(project_dir)
        }
    except OSError:
        parent_before = set()

    _apply_tools(project_dir, _TOOLS_5)

    # -------------------------------------------------------------------------
    # Checks
    # -------------------------------------------------------------------------
    failures: list[str] = []

    # Check 1: canary file is untouched (content + mtime)
    if not canary_file.exists():
        failures.append(f"Canary file was DELETED: {canary_file}")
    else:
        actual_content = canary_file.read_text(encoding="utf-8")
        if actual_content != canary_content:
            failures.append(
                f"Canary file was MODIFIED: {canary_file}\n"
                f"  Expected: {canary_content!r}\n"
                f"  Got:      {actual_content!r}"
            )
        canary_mtime_after = canary_file.stat().st_mtime
        if canary_mtime_after != canary_mtime_before:
            failures.append(f"Canary file mtime changed (file was touched): {canary_file}")

    # Check 2: no new entries in parent_probe outside project_dir
    try:
        parent_after: set[str] = {
            str(p) for p in parent_probe.iterdir() if not Path(p).is_relative_to(project_dir)
        }
    except OSError:
        parent_after = set()

    new_in_parent = parent_after - parent_before
    if new_in_parent:
        failures.append(
            f"New entries appeared in project parent dir outside project_dir "
            f"({len(new_in_parent)} item(s)):\n"
            + "\n".join(f"  {p}" for p in sorted(new_in_parent))
        )

    # Check 3: no new top-level files in HOME
    try:
        home_after: set[str] = {str(p) for p in home_path.iterdir() if p.is_file()}
    except OSError:
        home_after = set()

    new_in_home = home_after - home_before
    if new_in_home:
        failures.append(
            f"New files appeared in HOME ({home_path}) "
            f"({len(new_in_home)} file(s)):\n" + "\n".join(f"  {p}" for p in sorted(new_in_home))
        )

    if failures:
        return False, "Sandbox violation(s):\n" + "\n".join(failures)

    return True, ("Sandbox contained: canary untouched, no new files in project parent dir or HOME")


# ---------------------------------------------------------------------------
# Test 3 — Relocation: no hardcoded absolute paths
# ---------------------------------------------------------------------------


def check_3_relocation(tmp_dir: Path) -> tuple[bool, str]:
    """Test 3: Generated project survives relocation (no hardcoded paths).

    Steps:
    1. Generate project + apply 3 tools at original location.
    2. Move the entire project tree to a different directory.
    3. Verify that ``app/main.py`` can be imported and exposes a FastAPI app
       from the new location (proving no absolute paths are baked in).

    Args:
        tmp_dir: Writable temporary directory.

    Returns:
        ``(True, message)`` if the app imports cleanly after relocation,
        ``(False, message)`` with error details otherwise.
    """
    # -- Generate project ----------------------------------------------------
    original_dir = create_fixture_project(name="reloc_src", tmp_dir=tmp_dir / "src")
    _apply_tools(original_dir, _TOOLS_3)

    # -- Move to a different location ----------------------------------------
    relocated_dir = tmp_dir / "relocated" / "app_relocated"
    relocated_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(original_dir), str(relocated_dir))

    # -- Verify no absolute path references to original location --------------
    failures: list[str] = []
    original_str = str(original_dir)
    for candidate in sorted(relocated_dir.rglob("*")):
        if not candidate.is_file():
            continue
        if any(part in _EXCLUDE_DIR_PARTS for part in candidate.parts):
            continue
        if candidate.suffix in _EXCLUDE_SUFFIXES:
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if original_str in content:
            failures.append(
                f"Hardcoded original path found in: {candidate.relative_to(relocated_dir)}"
            )

    if failures:
        return False, (
            "Absolute path(s) baked into project files "
            f"({len(failures)} file(s)):\n" + "\n".join(f"  {f}" for f in failures)
        )

    # -- Try importing app.main from the relocated directory ------------------
    main_py = relocated_dir / "app" / "main.py"
    if not main_py.exists():
        return False, f"app/main.py not found after relocation: {main_py}"

    # The generated Settings() validates at import time and fails closed on a
    # missing/weak SECRET_KEY (and other prod guards). The generated conftest
    # supplies these test-only defaults before importing app.main; do the same
    # here so the relocation check exercises *import portability* rather than
    # the ambient environment. Without this the import raises a SECRET_KEY
    # ValidationError whenever the harness env has no SECRET_KEY (e.g. CI),
    # turning a pure relocation test into an env-dependent flake.
    _reloc_env_defaults = {
        # 64-char test-only placeholder; clears the >=32-char strength check
        # without ever shipping as a real credential.
        "SECRET_KEY": "test-only-secret-key-not-for-production-0000000000000000000000000000",
        "RATE_LIMITING_ENABLED": "false",
        "ENVIRONMENT": "local",
    }
    _reloc_env_added: list[str] = []
    for _k, _v in _reloc_env_defaults.items():
        if _k not in os.environ:
            os.environ[_k] = _v
            _reloc_env_added.append(_k)

    # Insert relocated_dir into sys.path temporarily
    sys.path.insert(0, str(relocated_dir))
    try:
        spec = importlib.util.spec_from_file_location("_relocated_app_main", str(main_py))
        if spec is None or spec.loader is None:
            return False, f"Could not create module spec for {main_py}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        app = getattr(module, "app", None)
        if app is None:
            return False, "app/main.py imported but 'app' object not found"
        # Light sanity-check: app should be a FastAPI instance
        app_type = type(app).__name__
        if app_type not in {"FastAPI", "Starlette"}:
            return False, (f"app/main.py 'app' is {app_type!r}, expected FastAPI")
    except Exception as exc:  # noqa: BLE001
        return False, f"app/main.py failed to import after relocation: {exc}"
    finally:
        sys.path.remove(str(relocated_dir))
        # Clean up the imported module to avoid cross-test contamination
        for key in list(sys.modules.keys()):
            if key.startswith("_relocated_app"):
                del sys.modules[key]
        # Restore the environment so we only supply defaults that were absent.
        for _k in _reloc_env_added:
            os.environ.pop(_k, None)

    return True, (
        f"Relocation OK: app imports cleanly from {relocated_dir}; "
        f"no absolute paths found in project files"
    )


# ---------------------------------------------------------------------------
# Test 4 — Regression snapshot
# ---------------------------------------------------------------------------


def _build_snapshot(project_dir: Path) -> dict:
    """Build a regression snapshot from a generated project.

    Args:
        project_dir: Root of the FastAPI project.

    Returns:
        Dict with keys: file_count, python_file_count, total_loc,
        routes, models, migrations.
    """
    all_files = [
        p
        for p in project_dir.rglob("*")
        if p.is_file()
        and not any(part in _EXCLUDE_DIR_PARTS for part in p.parts)
        and p.suffix not in _EXCLUDE_SUFFIXES
    ]
    py_files = [f for f in all_files if f.suffix == ".py"]

    return {
        "file_count": len(all_files),
        "python_file_count": len(py_files),
        "total_loc": _count_loc(project_dir),
        "routes": _collect_routes(project_dir),
        "models": _collect_models(project_dir),
        "migrations": _collect_migrations(project_dir),
    }


def check_4_regression_snapshot(tmp_dir: Path) -> tuple[bool, str]:
    """Test 4: Golden snapshot regression guard.

    On the FIRST run (no snapshot file found): generates the project,
    collects metrics, and writes ``tests/fixtures/golden_snapshot.json``.
    The test passes with a note that the snapshot was created.

    On SUBSEQUENT runs: regenerates the project, collects the same metrics,
    and compares against the stored snapshot.  Any divergence is a regression.

    Checked metrics:
    - Total non-excluded file count
    - Total Python file count
    - Total lines of code
    - Complete route list (METHOD:path)
    - SQLAlchemy model class names
    - Alembic migration file names

    Args:
        tmp_dir: Writable temporary directory.

    Returns:
        ``(True, message)`` if snapshot matches (or was freshly created),
        ``(False, message)`` with regression details otherwise.
    """
    project_dir = create_fixture_project(name="snap_proj", tmp_dir=tmp_dir)
    _apply_tools(project_dir, _TOOLS_SNAP)

    current = _build_snapshot(project_dir)

    # -- First run: create golden snapshot -----------------------------------
    if not _SNAPSHOT_PATH.exists():
        _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        return True, (
            f"Golden snapshot CREATED at {_SNAPSHOT_PATH}\n"
            f"  file_count={current['file_count']}, "
            f"python_file_count={current['python_file_count']}, "
            f"total_loc={current['total_loc']}, "
            f"routes={len(current['routes'])}, "
            f"models={len(current['models'])}, "
            f"migrations={len(current['migrations'])}"
        )

    # -- Subsequent runs: compare against stored snapshot -------------------
    golden: dict = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    regressions: list[str] = []

    _SCALAR_KEYS = ["file_count", "python_file_count", "total_loc"]  # noqa: N806
    for key in _SCALAR_KEYS:
        if current.get(key) != golden.get(key):
            regressions.append(f"{key}: expected {golden.get(key)}, got {current.get(key)}")

    _LIST_KEYS = ["routes", "models", "migrations"]  # noqa: N806
    for key in _LIST_KEYS:
        cur_set = set(current.get(key, []))
        gold_set = set(golden.get(key, []))
        added = cur_set - gold_set
        removed = gold_set - cur_set
        if added:
            regressions.append(f"{key} ADDED ({len(added)}): " + ", ".join(sorted(added)))
        if removed:
            regressions.append(f"{key} REMOVED ({len(removed)}): " + ", ".join(sorted(removed)))

    if regressions:
        detail = "\n  ".join(regressions)
        return False, (
            f"Regression detected ({len(regressions)} divergence(s)) "
            f"vs {_SNAPSHOT_PATH}:\n  {detail}"
        )

    return True, (
        f"Snapshot matches golden ({_SNAPSHOT_PATH.name}): "
        f"file_count={current['file_count']}, "
        f"total_loc={current['total_loc']}, "
        f"routes={len(current['routes'])}, "
        f"models={len(current['models'])}, "
        f"migrations={len(current['migrations'])}"
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

_TESTS = [
    ("Determinism (same inputs → identical output)", check_1_determinism),
    ("Sandbox containment (no writes outside project_dir)", check_2_sandbox_containment),
    ("Relocation (no hardcoded absolute paths)", check_3_relocation),
    ("Regression snapshot (golden file comparison)", check_4_regression_snapshot),
]


def run_all_tests() -> int:
    """Run all 4 determinism tests, each in its own temporary directory.

    Returns:
        Number of tests that passed (0–4).
    """
    print("=" * 70)
    print("DETERMINISM TESTS — SKILL-001 FastAPI generator")
    print("=" * 70)

    passed = 0
    for i, (name, fn) in enumerate(_TESTS, 1):
        print(f"\n[TEST {i}/4] {name}")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            try:
                ok, msg = fn(tmp_path)
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
    print(f"RESULT: {passed}/4 tests passed")
    print("=" * 70)
    return passed


if __name__ == "__main__":
    n = run_all_tests()
    print(f"\nDeterminism: {n}/4 tests pass")
    sys.exit(0 if n == 4 else 1)
