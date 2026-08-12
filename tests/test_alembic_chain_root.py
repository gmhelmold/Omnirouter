"""Regression test for R6-O4-A1 + R6-O4-A2.

R6-O4-A1: The base scaffold must always emit ``alembic/versions/0001_initial.py``
so that extend tools — which chain off ``down_revision = "0001_initial"`` —
have a valid parent revision.  Without this, ``alembic upgrade head`` fails
with ``Can't locate revision identified by '0001_initial'``.

R6-O4-A2: ``refactor_model`` previously emitted migrations with
``down_revision = None``, forking the chain whenever any prior migration
already existed.  The fix wires ``down_revision`` to the current chain HEAD
via :func:`adapt.contracts.migration_helper.find_migration_head`.

These tests fail on main pre-fix and pass after.

Run standalone::

    PYTHONPATH=. pytest tests/test_alembic_chain_root.py -v
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

import pytest  # noqa: E402

from adapt.contracts import ToolInput  # noqa: E402
from adapt.contracts.migration_helper import (  # noqa: E402
    MigrationChainError,
    find_migration_head,
)
from tests.common.fixture_factory import create_fixture_project  # noqa: E402


# ---------------------------------------------------------------------------
# Migration chain parsing
# ---------------------------------------------------------------------------

_REV_RE = re.compile(
    r'^revision\s*(?::\s*[^=]+?)?\s*=\s*["\']([^"\']+)["\']', re.MULTILINE
)
_DOWN_RE = re.compile(
    r'^down_revision\s*(?::\s*[^=]+?)?\s*=\s*(?:["\']([^"\']*)["\']|(None))',
    re.MULTILINE,
)


def _parse_chain(versions_dir: Path) -> dict[str, str | None]:
    """Return ``{revision: down_revision}`` for every migration file."""
    chain: dict[str, str | None] = {}
    for f in sorted(versions_dir.glob("*.py")):
        if f.name.startswith("__"):
            continue
        src = f.read_text(encoding="utf-8")
        rev_m = _REV_RE.search(src)
        if not rev_m:
            continue
        rev = rev_m.group(1)
        down_m = _DOWN_RE.search(src)
        if down_m is None:
            chain[rev] = None
        elif down_m.group(1) is not None:
            chain[rev] = down_m.group(1)
        else:
            chain[rev] = None
    return chain


def _assert_chain_consistent(chain: dict[str, str | None]) -> None:
    """Fail if any down_revision points to a revision not on disk.

    This is the in-process equivalent of ``alembic upgrade head`` succeeding:
    every non-None down_revision must resolve to a known revision, and there
    must be exactly one root (down_revision is None).
    """
    revisions = set(chain.keys())
    missing = [
        (rev, dr) for rev, dr in chain.items() if dr is not None and dr not in revisions
    ]
    assert not missing, (
        "Migration chain references nonexistent revisions: "
        + ", ".join(f"{rev} -> down_revision={dr!r}" for rev, dr in missing)
        + f". Known revisions on disk: {sorted(revisions)}"
    )

    roots = [rev for rev, dr in chain.items() if dr is None]
    assert len(roots) == 1, (
        f"Expected exactly one chain root (down_revision=None); got {roots}"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scaffold_emits_0001_initial_chain_root() -> None:
    """R6-O4-A1: a fresh project must contain a ``0001_initial`` revision on disk.

    Without this file, every extend tool's
    ``find_migration_head(versions_dir) or "0001_initial"`` fallback writes a
    migration whose down_revision points at a nonexistent parent.

    The fix moves the no-op ``0001_initial`` baseline into
    ``generators.database.alembic.generate_alembic`` itself (rather than
    only emitting it as a side effect of ``generate_baseline_migration``),
    so the chain root exists even when ``generate_baseline_migration``
    runs without models.
    """
    project = create_fixture_project(
        name="scaffold_chain_root",
        models={"Widget": {"name": "str"}},
    )
    versions_dir = project / "alembic" / "versions"
    chain = _parse_chain(versions_dir)
    assert "0001_initial" in chain, (
        f"alembic/versions/ is missing the 0001_initial chain root. "
        f"Found: {sorted(chain.keys())}"
    )
    assert chain["0001_initial"] is None, (
        f"0001_initial must be the chain root (down_revision=None); "
        f"got down_revision={chain['0001_initial']!r}"
    )
    _assert_chain_consistent(chain)


def test_scaffold_emits_0001_initial_as_dedicated_file() -> None:
    """R6-O4-A1: 0001_initial.py must exist as a dedicated file in versions/.

    Pre-fix the chain root was tangled into ``0001_initial_schema.py`` and
    only emitted when ``generate_baseline_migration`` ran with models or
    auth.  The fix makes ``generate_alembic`` always emit a dedicated
    ``0001_initial.py`` no-op so the chain root is guaranteed even if the
    baseline-schema generator is skipped.
    """
    project = create_fixture_project(
        name="scaffold_dedicated_root",
        models={"Widget": {"name": "str"}},
    )
    initial_file = project / "alembic" / "versions" / "0001_initial.py"
    assert initial_file.exists(), (
        f"R6-O4-A1: alembic/versions/0001_initial.py is missing. "
        f"Files present: "
        f"{[p.name for p in (project / 'alembic' / 'versions').glob('*.py')]}"
    )
    src = initial_file.read_text()
    assert 'revision: str = "0001_initial"' in src or 'revision = "0001_initial"' in src


def test_extend_tool_produces_resolvable_chain() -> None:
    """R6-O4-A1: scaffold + one extend tool → ``alembic upgrade head`` is valid.

    Without the fix, ``add_soft_delete`` emits a migration whose
    ``down_revision = "0001_initial"`` points at a parent that never existed,
    and ``alembic upgrade head`` errors out at chain resolution time.
    """
    from adapt.extend.crud_data.add_soft_delete import add_soft_delete

    project = create_fixture_project(
        name="extend_chain_root",
        models={"Widget": {"name": "str"}},
    )
    versions_dir = project / "alembic" / "versions"

    result = add_soft_delete(ToolInput(project_dir=str(project)))
    assert result.status == "success", f"add_soft_delete failed: {result.error}"

    chain = _parse_chain(versions_dir)
    _assert_chain_consistent(chain)

    # And the helper agrees on the new HEAD.
    head = find_migration_head(versions_dir)
    assert head is not None
    # HEAD is whatever soft-delete emitted (not the baseline).
    assert head != "0001_initial", (
        "After running an extend tool the chain HEAD must move past 0001_initial"
    )


def test_migration_helper_raises_on_missing_chain_root() -> None:
    """R6-O4-A1 helper hardening: empty versions/ → loud failure, not silent fallback.

    Previously ``find_migration_head`` returned ``None`` on an empty directory
    and callers silently fell back to the string ``"0001_initial"`` — which
    broke ``alembic upgrade head`` when no such revision existed.  The helper
    now raises :class:`MigrationChainError` so the scaffold-integrity failure
    is visible.
    """
    with tempfile.TemporaryDirectory() as tmp:
        empty_versions = Path(tmp) / "alembic" / "versions"
        empty_versions.mkdir(parents=True)

        with pytest.raises(MigrationChainError):
            find_migration_head(empty_versions)

        # Missing directory: also raises.
        missing = Path(tmp) / "does_not_exist"
        with pytest.raises(MigrationChainError):
            find_migration_head(missing)


def test_migration_helper_resolves_chain_root() -> None:
    """find_migration_head returns the chain root when only 0001_initial exists."""
    with tempfile.TemporaryDirectory() as tmp:
        versions = Path(tmp) / "alembic" / "versions"
        versions.mkdir(parents=True)
        (versions / "0001_initial.py").write_text(
            'revision = "0001_initial"\n'
            "down_revision = None\n"
            "branch_labels = None\n"
            "depends_on = None\n"
            "def upgrade() -> None: pass\n"
            "def downgrade() -> None: pass\n"
        )
        head = find_migration_head(versions)
        assert head == "0001_initial"


def test_refactor_model_chains_to_head_not_none() -> None:
    """R6-O4-A2: refactor_model migration must chain to current HEAD, not None.

    Previously the template hard-coded ``down_revision = None``, forking the
    chain whenever any other migration existed.
    """
    from adapt.evolve.refactor_model import refactor_model

    project = create_fixture_project(
        name="refactor_chain",
        models={"Widget": {"name": "str"}},
    )
    versions_dir = project / "alembic" / "versions"
    pre_chain = _parse_chain(versions_dir)
    pre_head = find_migration_head(versions_dir)
    assert pre_head is not None

    result = refactor_model(
        ToolInput(project_dir=str(project)),
        operation="rename_field",
        target="app.models.widget.Widget.name",
        new_name="title",
        generate_migration=True,
    )
    assert result.status == "success", f"refactor_model failed: {result.error}"

    post_chain = _parse_chain(versions_dir)
    new_revs = set(post_chain) - set(pre_chain)
    assert new_revs, "refactor_model did not emit a migration"

    for rev in new_revs:
        dr = post_chain[rev]
        assert dr is not None, (
            f"R6-O4-A2: refactor_model emitted {rev} with down_revision=None — "
            "forks the chain"
        )
        assert dr == pre_head, (
            f"R6-O4-A2: refactor_model emitted {rev} with down_revision={dr!r}; "
            f"expected pre-emit HEAD {pre_head!r}"
        )

    _assert_chain_consistent(post_chain)


def test_alembic_resolves_full_chain_after_extend() -> None:
    """R6-O4-A1 end-to-end: ``ScriptDirectory.walk_revisions`` resolves cleanly.

    Drives the actual alembic chain-resolution machinery (the same code path
    ``alembic upgrade head`` would execute) against the on-disk versions
    directory.  If any migration references a nonexistent down_revision,
    alembic raises ``ResolutionError`` here — exactly the failure mode the
    finding describes.

    Skipped when alembic is not installed in the test environment.
    """
    alembic = pytest.importorskip("alembic")
    from alembic.script import ScriptDirectory
    from alembic.config import Config

    from adapt.extend.crud_data.add_soft_delete import add_soft_delete

    project = create_fixture_project(
        name="alembic_walk_revisions",
        models={"Widget": {"name": "str"}},
    )
    versions_dir = project / "alembic" / "versions"

    sd_result = add_soft_delete(ToolInput(project_dir=str(project)))
    assert sd_result.status == "success", f"add_soft_delete failed: {sd_result.error}"

    # Also exercise refactor_model (R6-O4-A2 regression) so a multi-head
    # fork would surface here as a ScriptDirectory.get_heads() length > 1.
    from adapt.evolve.refactor_model import refactor_model

    rm_result = refactor_model(
        ToolInput(project_dir=str(project)),
        operation="rename_field",
        target="app.models.widget.Widget.name",
        new_name="title",
        generate_migration=True,
    )
    assert rm_result.status == "success", f"refactor_model failed: {rm_result.error}"

    cfg = Config()
    cfg.set_main_option("script_location", str(project / "alembic"))

    script = ScriptDirectory.from_config(cfg)
    # walk_revisions raises if a down_revision points at a missing parent —
    # this is the same resolution code that alembic upgrade head uses.
    revisions = list(script.walk_revisions())
    assert revisions, "alembic found no revisions"

    heads = script.get_heads()
    assert len(heads) == 1, (
        f"R6-O4-A1/A2: alembic detects multiple heads {heads}; "
        f"the chain must be a single line after the fix"
    )


def test_all_migration_files_are_valid_python() -> None:
    """Every emitted migration must ast.parse cleanly."""
    project = create_fixture_project(
        name="parse_check",
        models={"Widget": {"name": "str"}},
    )
    versions_dir = project / "alembic" / "versions"
    files = list(versions_dir.glob("*.py"))
    assert files, "scaffold produced no migrations"
    for f in files:
        src = f.read_text(encoding="utf-8")
        try:
            ast.parse(src)
        except SyntaxError as exc:
            raise AssertionError(f"SyntaxError in {f}: {exc}") from exc
