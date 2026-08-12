"""Generic tool-contract mutation coverage.

The adapt/extend ``add_*`` tools share one structure (validate project ->
ensure_prerequisites -> dry_run guard -> idempotency no_op guard -> write files
-> ToolResult with execution_time_ms). The mutants in that shared "preamble"
are identical across the whole fleet, so we assert them ONCE here and apply the
checks to every tool's test via a tiny parametrized stub — instead of
re-deriving the same tests per tool.

Each check takes the tool's ``add_fn`` and a unique ``name`` (for fixture dir
isolation). ``SCAFFOLDABLE_CHECKS`` apply to tools whose only prerequisites are
auto-scaffoldable (CONFIG_SETTINGS / REQUIREMENTS_TXT) — i.e. a bare project
run succeeds. ``UNIVERSAL_CHECKS`` apply to every tool.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from adapt.contracts import ToolInput
from tests.common.fixture_factory import create_fixture_project


def _bare(name: str) -> Path:
    d = Path(tempfile.mkdtemp()) / f"bare_{name}"
    d.mkdir(parents=True)
    return d


def _all_py(root: Path) -> dict[Path, str]:
    return {f: f.read_text() for f in root.rglob("*.py")}


# --- universal: apply to every tool ----------------------------------------


def check_success(add_fn, name: str) -> None:
    """A clean run on a full fixture project succeeds (happy-path baseline)."""
    p = create_fixture_project(name=f"{name}_ok")
    r = add_fn(ToolInput(project_dir=str(p)))
    assert r.status == "success", r.error


def check_execution_time_bound(add_fn, name: str) -> None:
    """execution_time_ms is a small positive number — kills the
    ``(monotonic() - start)`` BinOp and ``* 1000`` mutants (a sign/op flip
    yields a huge or zero value)."""
    p = create_fixture_project(name=f"{name}_ct")
    ms = add_fn(ToolInput(project_dir=str(p))).execution_time_ms
    assert 0 < ms < 60_000, f"implausible execution_time_ms={ms}"


def check_idempotent(add_fn, name: str) -> None:
    """Second run is a no_op — kills the idempotency-guard Compare/BoolOp."""
    p = create_fixture_project(name=f"{name}_id")
    first = add_fn(ToolInput(project_dir=str(p)))
    assert first.status == "success", first.error
    assert add_fn(ToolInput(project_dir=str(p))).status == "no_op"


def check_dry_run_writes_nothing(add_fn, name: str) -> None:
    """dry_run mutates no .py file — kills the dry_run-guard mutants."""
    p = create_fixture_project(name=f"{name}_dr")
    before = _all_py(p)
    add_fn(ToolInput(project_dir=str(p), dry_run=True))
    assert _all_py(p) == before


# --- scaffoldable-only tools: a bare project run succeeds -------------------


def check_bare_autoscaffold(add_fn, name: str) -> None:
    """On a bare project (missing the scaffoldable prereqs), a real run with
    dry_run=False auto-scaffolds and SUCCEEDS, reporting the scaffolded files.
    Kills ``auto_scaffold=not inp.dry_run`` (UnaryNot) and
    ``list(scaffolded or [])`` (BoolOp Or->And)."""
    p = _bare(f"{name}_as")
    r = add_fn(ToolInput(project_dir=str(p)))
    assert r.status == "success", r.error
    assert r.files_created, "auto-scaffold produced no files_created"


def check_bare_dry_run_prereq_error(add_fn, name: str) -> None:
    """On a bare project with dry_run=True, auto_scaffold is OFF, so the
    prereqs are missing and the tool returns a prereq error — kills the
    error-message ``+`` concat (str - str would raise instead)."""
    p = _bare(f"{name}_pe")
    r = add_fn(ToolInput(project_dir=str(p), dry_run=True))
    assert r.status == "error"
    assert "Prerequisites" in (r.error or "")


def check_mkdir_exist_ok(add_fn, name: str) -> None:
    """When the directories a tool creates already exist, the run still
    succeeds — kills the ``mkdir(exist_ok=True)`` BoolLiteral (exist_ok=False
    would raise FileExistsError). Generic: discover the created files on one
    project, pre-create their parent dirs on a second, then run."""
    a = create_fixture_project(name=f"{name}_e1")
    created = add_fn(ToolInput(project_dir=str(a))).files_created
    b = create_fixture_project(name=f"{name}_e2")
    a_root, b_root = Path(a), Path(b)
    made = False
    for fc in created:
        try:
            rel = Path(fc).relative_to(a_root)
        except ValueError:
            continue
        (b_root / rel).parent.mkdir(parents=True, exist_ok=True)
        made = True
    if not made:
        return  # tool creates no files under the project — nothing to assert
    r = add_fn(ToolInput(project_dir=str(b)))
    assert r.status == "success", r.error


UNIVERSAL_CHECKS = [
    check_success,
    check_execution_time_bound,
    check_idempotent,
    check_dry_run_writes_nothing,
    check_mkdir_exist_ok,
]

SCAFFOLDABLE_CHECKS = UNIVERSAL_CHECKS + [
    check_bare_autoscaffold,
    check_bare_dry_run_prereq_error,
]
