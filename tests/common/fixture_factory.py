"""Factory that generates real FastAPI projects for use in adapt tool tests.

Uses ``generators.orchestrator.generate_project`` to produce a fully-wired
project in a temporary directory, then validates that every generated Python
file parses without ``SyntaxError``.

Typical usage inside a test::

    from tests.common.fixture_factory import create_fixture_project

    project_dir = create_fixture_project(name="myapp")
    # project_dir is a Path pointing to a fresh, valid FastAPI project
"""

from __future__ import annotations

import ast
import atexit
import secrets
import shutil
import tempfile
from pathlib import Path

from generators.orchestrator import generate_project

# Test fixture SECRET_KEY — 64 hex chars, generated once per-process.
# Matches the shape of a real `openssl rand -hex 32` output so the
# scaffold's fail-fast validator (≥ 32 chars, not in _WEAK_CREDENTIALS)
# accepts it. Regenerating per-process keeps tests isolated from a
# stale value baked into a previous run.
_FIXTURE_SECRET_KEY: str = secrets.token_hex(32)

# Throwaway project dirs this factory created with its own mkdtemp (i.e. when
# the caller passed tmp_dir=None). The suite scaffolds thousands of these; if
# never removed they leak GBs (≈2 GB/run) — fine on an ephemeral CI VM, but on a
# persistent self-hosted runner they fill the disk. Callers don't own these
# dirs, so the factory tracks them and `purge_tracked_dirs()` reclaims them.
# An atexit hook is the backstop for standalone runs (audit_l1 / `python tests/
# foo.py`); pytest runs also purge per test-module via conftest for a tighter
# bound during the run.
_TRACKED_DIRS: list[Path] = []


def purge_tracked_dirs() -> None:
    """Delete every factory-created mkdtemp dir tracked so far, then forget them."""
    while _TRACKED_DIRS:
        d = _TRACKED_DIRS.pop()
        shutil.rmtree(d, ignore_errors=True)


atexit.register(purge_tracked_dirs)


def create_fixture_project(
    name: str = "test_project",
    models: dict | None = None,
    tmp_dir: Path | None = None,
    with_auth: bool = True,
) -> Path:
    """Generate a fresh FastAPI project for testing.

    Uses ``generators.orchestrator.generate_project`` to produce a real
    project tree, then validates that every generated ``.py`` file parses
    via ``ast.parse``.

    Args:
        name: Application name (also used as the output directory name
            inside ``tmp_dir``).
        models: Domain models to generate.  Defaults to a minimal
            ``{"Item": {"title": "str", "description": "str"}}`` with an
            ``Item -> user`` owner relationship so soft-delete tests have a
            concrete model to target.
        tmp_dir: Parent directory in which to create the project.  When
            ``None`` a fresh ``tempfile.mkdtemp()`` directory is used and
            the project is placed directly inside it.
        with_auth: Whether to generate the full auth stack.  Defaults to
            ``True`` so that ``CurrentUser`` / ``CurrentSuperuser`` deps
            and the ``users.id`` FK used by soft-delete are available.

    Returns:
        ``Path`` pointing to the generated project root (contains ``app/``,
        ``alembic/``, ``requirements.txt``, etc.).

    Raises:
        SyntaxError: If any generated ``.py`` file fails ``ast.parse``.
        ValueError: If ``generate_project`` returns no files.
    """
    if models is None:
        models = {"Item": {"title": "str", "description": "str"}}

    if tmp_dir is None:
        # Factory owns this dir → track it for purge_tracked_dirs(). When the
        # caller supplies tmp_dir they own its lifecycle, so we don't track it.
        base = Path(tempfile.mkdtemp())
        _TRACKED_DIRS.append(base)
        output_dir = base / name
    else:
        output_dir = Path(tmp_dir) / name

    owner_models = {m: "user" for m in models if m != "User"} if with_auth else None

    result = generate_project(
        output_dir=str(output_dir),
        name=name,
        models=models,
        owner_models=owner_models,
        with_auth=with_auth,
        with_docker_compose=False,
        with_ci=False,
        with_otel=False,
        with_prometheus=False,
    )

    if not result.get("files_created"):
        raise ValueError(
            f"generate_project produced no files for project '{name}' at {output_dir}"
        )

    _assert_all_py_parse(output_dir)

    # Write a valid .env so the generated project boots.
    # The scaffold's Settings validator (app/core/config.py) fail-fasts
    # on empty / short / known-weak SECRET_KEY values — in every env,
    # LOCAL included — so test subprocesses that do
    # `from app.main import app` need a real 32-char+ key in scope.
    # Writing .env here mimics the real dev workflow and keeps test
    # environments from inheriting leaked parent-shell secrets.
    _write_env_fixture(output_dir)

    return output_dir


def _write_env_fixture(project_root: Path) -> None:
    """Write a minimal `.env` into *project_root* so the generated app boots.

    Only writes when no `.env` already exists so callers that want to
    exercise a specific environment (e.g. a missing-SECRET_KEY negative
    test) can write their own first.
    """
    env_path = project_root / ".env"
    if env_path.exists():
        return
    env_path.write_text(
        "# Generated by tests.common.fixture_factory for test-only use.\n"
        f"SECRET_KEY={_FIXTURE_SECRET_KEY}\n"
        "ENVIRONMENT=local\n"
        "# FIRST_SUPERUSER_* left empty on purpose → no seed during tests.\n"
        "FIRST_SUPERUSER_EMAIL=\n"
        "FIRST_SUPERUSER_PASSWORD=\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_all_py_parse(project_root: Path) -> None:
    """Raise ``SyntaxError`` if any ``.py`` file under *project_root* is invalid.

    Args:
        project_root: Root directory to walk recursively.

    Raises:
        SyntaxError: With the file path and original error message included
            so failing tests surface the exact culprit file immediately.
    """
    for py_file in sorted(project_root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except SyntaxError as exc:
            raise SyntaxError(
                f"Generated file {py_file} has a syntax error: {exc}"
            ) from exc
