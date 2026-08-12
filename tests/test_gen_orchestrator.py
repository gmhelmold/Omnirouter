"""Tests for generator: orchestrator."""

import pytest
import tempfile
import shutil
from pathlib import Path


def test_generator_orchestrator_creates_files():
    """Generator must run successfully and create files."""
    from generators.orchestrator import generate_project

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_project(output_dir, name='testapp', models={'Item': {'name': 'str'}})
        assert isinstance(result, dict)
        assert "files_created" in result
        assert isinstance(result["files_created"], list)
        assert len(result["files_created"]) > 0, "Generator created no files"
    finally:
        shutil.rmtree(output_dir)


def test_generator_orchestrator_idempotent():
    """Running twice must be safe — second run succeeds or is no-op."""
    from generators.orchestrator import generate_project

    output_dir = tempfile.mkdtemp()
    try:
        result1 = generate_project(output_dir, name='testapp', models={'Item': {'name': 'str'}})
        result2 = generate_project(output_dir, name='testapp', models={'Item': {'name': 'str'}})
        assert isinstance(result2, dict)
    finally:
        shutil.rmtree(output_dir)


def test_generator_orchestrator_valid_python():
    """Generated .py files must pass ast.parse."""
    from generators.orchestrator import generate_project

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_project(output_dir, name='testapp', models={'Item': {'name': 'str'}})
        for fpath in result.get("files_created", []):
            if fpath.endswith(".py"):
                full_path = Path(output_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
                source = full_path.read_text()
                import ast
                ast.parse(source)
    finally:
        shutil.rmtree(output_dir)


def test_generator_orchestrator_files_exist():
    """All files_created must exist on disk after generation."""
    from generators.orchestrator import generate_project

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_project(output_dir, name='testapp', models={'Item': {'name': 'str'}})
        for fpath in result.get("files_created", []):
            full_path = Path(output_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
            assert full_path.exists(), f"File not found: {fpath}"
    finally:
        shutil.rmtree(output_dir)
