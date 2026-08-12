"""Tests for generator: migrate_db (tool)."""

import pytest
import tempfile
import shutil
from pathlib import Path


def test_generator_migrate_db_creates_files():
    """Tool must run successfully and return files_created or notes."""
    from generators.tools.migrate_db import generate_migration

    project_dir = tempfile.mkdtemp()
    try:
        result = generate_migration(project_dir=project_dir)
        assert isinstance(result, dict)
        assert "notes" in result
    finally:
        shutil.rmtree(project_dir)


def test_generator_migrate_db_idempotent():
    """Running twice must be safe — second run skips or succeeds."""
    from generators.tools.migrate_db import generate_migration

    project_dir = tempfile.mkdtemp()
    try:
        result1 = generate_migration(project_dir=project_dir)
        result2 = generate_migration(project_dir=project_dir)
        assert isinstance(result2, dict)
    finally:
        shutil.rmtree(project_dir)


def test_generator_migrate_db_valid_python():
    """Generated .py files must pass ast.parse."""
    from generators.tools.migrate_db import generate_migration

    project_dir = tempfile.mkdtemp()
    try:
        result = generate_migration(project_dir=project_dir)
        files = result.get("files_created", []) + result.get("files_modified", [])
        for fpath in files:
            if fpath.endswith(".py"):
                full_path = Path(project_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
                source = full_path.read_text()
                import ast
                ast.parse(source)
    finally:
        shutil.rmtree(project_dir)
