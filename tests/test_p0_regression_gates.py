"""P0 regression gates — turn the round-2 external-eval findings into permanent guards.

The round-1 fixes were patched without a regression net, so making multi_tenancy
"real" reopened a worse class of bugs that round-2's cold panel caught. These gates
encode the *class* of each P0 finding so it cannot silently return:

  GATE 1 (composition-green): a contract-changing compose tool MUST keep the
      emitted project's own pytest suite green. Catches the multi_tenancy class
      (tool reports success while breaking ~86% of the emitted tests).

  GATE 2 (no raw-SQL interpolation): no emitted module may pass a non-literal
      argument to SQLAlchemy text()/_text(). Catches the search-autocomplete
      SQLi class (user input concatenated into a raw SQL literal).

Run: PYTHONPATH=. SECRET_KEY=... ENVIRONMENT=local .venv/bin/python tests/test_p0_regression_gates.py
Requires the same env as the other harnesses; GATE 1 runs the emitted pytest in a
subprocess against SQLite, so no external services are needed.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent

# Compose tools that change the emitted API/DB contract and therefore MUST
# keep the emitted test suite green after they run. Extend this list as new
# contract-changing tools land — that is the point of the gate.
#
# F-002 (Codex C1): the original list covered only multi_tenancy + soft_delete,
# leaving every other contract-mutating tool free of a green-suite gate. The
# list below now covers every compose tool that:
#   (a) mutates routes (adds, renames, removes) — payload/path/method contract;
#   (b) mutates the SQLAlchemy model layer — DB contract; or
#   (c) emits its own pytest file that becomes part of the suite — a
#       regression in the emitted test is a regression in the green-suite
#       contract for the next tool that runs after it.
# When adding a new compose tool that fits any of (a)(b)(c), add it here.
_CONTRACT_CHANGING_TOOLS: list[tuple[str, str]] = [
    # Auth / access — contract-changing across routes + DB layer.
    ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
    ("add_bola_guard", "adapt.extend.auth_access.add_bola_guard"),
    # CRUD / data — routes + emitted tests.
    ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete"),
    ("add_cursor_pagination", "adapt.extend.crud_data.add_cursor_pagination"),
    ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
    ("add_event_sourcing", "adapt.extend.crud_data.add_event_sourcing"),
    ("add_data_export", "adapt.extend.crud_data.add_data_export"),
    ("add_data_versioning", "adapt.extend.crud_data.add_data_versioning"),
    ("add_bulk_operations", "adapt.extend.crud_data.add_bulk_operations"),
    ("add_search", "adapt.extend.crud_data.add_search"),
    ("add_file_upload", "adapt.extend.crud_data.add_file_upload"),
]

_SCRATCH_MODELS = {
    "Project": {"name": "str", "description": "text", "status": "str"},
    "Task": {"title": "str", "notes": "text", "done": "bool"},
    "Comment": {"body": "text", "author_name": "str"},
}


def _scaffold(out: Path, name: str) -> None:
    from generators.orchestrator import generate_project

    generate_project(
        str(out),
        name,
        models=_SCRATCH_MODELS,
        owner_models={"Project": "user", "Task": "user"},
        with_auth=True,
        with_docker_compose=False,
        with_ci=False,
        with_otel=False,
        with_prometheus=False,
    )


def _write_env(project: Path) -> None:
    (project / ".env").write_text(
        "SECRET_KEY=" + "a" * 64 + "\n"
        "ENVIRONMENT=local\n"
        "DATABASE_URL=sqlite+aiosqlite:///./gate.db\n"
        "RATE_LIMITING_ENABLED=false\n"
        "FIRST_SUPERUSER_EMAIL=admin@example.com\n"
        "FIRST_SUPERUSER_PASSWORD=testpassword123\n"
    )


# ---------------------------------------------------------------------------
# GATE 2 — no raw-SQL interpolation of non-literal args into text()/_text()
# ---------------------------------------------------------------------------


def _text_violations(py_file: Path) -> list[str]:
    """Return offending lines where text()/_text() is called with a non-literal arg.

    A bound parameter is safe; a string *literal* (e.g. a fixed regconfig name) is
    safe. Anything else — concatenation, f-string, a Name bound to user input —
    is a raw-SQL interpolation risk and is flagged.
    """
    try:
        tree = ast.parse(py_file.read_text())
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname = (
            fn.id
            if isinstance(fn, ast.Name)
            else (fn.attr if isinstance(fn, ast.Attribute) else "")
        )
        if fname not in {"text", "_text"}:
            continue
        for arg in node.args:
            # Safe: a plain string constant literal.
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                continue
            # Unsafe: concatenation, f-string, or any dynamic expression.
            out.append(
                f"{py_file}:{node.lineno}: {fname}() called with non-literal arg ({type(arg).__name__})"
            )
    return out


def gate_no_raw_sql_interpolation() -> list[str]:
    """GATE 2: scaffold + compose search-ish tools, scan emitted app/ for unsafe text()."""
    from adapt.contracts import ToolInput
    from adapt.extend.crud_data.add_cursor_pagination import add_cursor_pagination
    from adapt.extend.crud_data.add_search import add_search

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gate_sqli_") as tmp:
        out = Path(tmp) / "proj"
        _scaffold(out, "gatesqli")
        for fn in (add_search, add_cursor_pagination):
            fn(ToolInput(project_dir=str(out)))
        for py_file in (out / "app").rglob("*.py"):
            failures.extend(_text_violations(py_file))
    return failures


# ---------------------------------------------------------------------------
# GATE 1 — contract-changing compose tools keep the emitted pytest green
# ---------------------------------------------------------------------------


def gate_composition_keeps_tests_green() -> list[str]:
    """GATE 1: for each contract-changing tool, scaffold→compose→run emitted pytest."""
    import importlib

    from adapt.contracts import ToolInput

    failures: list[str] = []
    for tool_name, module_path in _CONTRACT_CHANGING_TOOLS:
        with tempfile.TemporaryDirectory(prefix=f"gate_green_{tool_name[:12]}_") as tmp:
            out = Path(tmp) / "proj"
            _scaffold(out, "gategreen")
            _write_env(out)
            fn = getattr(importlib.import_module(module_path), tool_name)
            result = fn(ToolInput(project_dir=str(out)))
            if getattr(result, "status", None) == "error":
                failures.append(f"{tool_name}: tool returned status=error: {result.error}")
                continue
            env = dict(os.environ)
            env["PYTHONPATH"] = str(out)
            env.setdefault("SECRET_KEY", "a" * 64)
            env["ENVIRONMENT"] = "local"
            env["RATE_LIMITING_ENABLED"] = "false"
            env["DATABASE_URL"] = "sqlite+aiosqlite:///./gate.db"
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
                cwd=str(out),
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-6:])
                failures.append(f"{tool_name}: emitted pytest FAILED after compose:\n{tail}")
    return failures


def main() -> int:
    print("=== P0 regression gates ===")
    rc = 0

    g2 = gate_no_raw_sql_interpolation()
    if g2:
        rc = 1
        print(f"  FAIL GATE 2 (raw-SQL interpolation): {len(g2)} violation(s)")
        for v in g2[:10]:
            print(f"    {v}")
    else:
        print("  PASS GATE 2: no non-literal text()/_text() in emitted code")

    g1 = gate_composition_keeps_tests_green()
    if g1:
        rc = 1
        print(f"  FAIL GATE 1 (composition-green): {len(g1)} tool(s) break the emitted suite")
        for v in g1:
            print(f"    {v}")
    else:
        print(
            f"  PASS GATE 1: emitted pytest stays green after {len(_CONTRACT_CHANGING_TOOLS)} contract-changing tools"
        )

    print("RESULT:", "ALL GATES PASS" if rc == 0 else "GATES FAILED")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
