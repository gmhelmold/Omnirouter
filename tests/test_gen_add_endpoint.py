"""Tests for generator: add_endpoint (tool)."""

import pytest
import tempfile
import shutil
from pathlib import Path


def test_generator_add_endpoint_creates_files():
    """Tool must run successfully and return files_created or notes."""
    from generators.tools.add_endpoint import add_endpoint

    project_dir = tempfile.mkdtemp()
    try:
        result = add_endpoint(project_dir=project_dir, route_file='api/routes/items.py', method='get', path='/items', name='list_items')
        assert isinstance(result, dict)
        assert "notes" in result
    finally:
        shutil.rmtree(project_dir)


def test_generator_add_endpoint_idempotent():
    """Running twice must be safe — second run skips or succeeds."""
    from generators.tools.add_endpoint import add_endpoint

    project_dir = tempfile.mkdtemp()
    try:
        result1 = add_endpoint(project_dir=project_dir, route_file='api/routes/items.py', method='get', path='/items', name='list_items')
        result2 = add_endpoint(project_dir=project_dir, route_file='api/routes/items.py', method='get', path='/items', name='list_items')
        assert isinstance(result2, dict)
    finally:
        shutil.rmtree(project_dir)


def test_generator_add_endpoint_valid_python():
    """Generated .py files must pass ast.parse."""
    from generators.tools.add_endpoint import add_endpoint

    project_dir = tempfile.mkdtemp()
    try:
        result = add_endpoint(project_dir=project_dir, route_file='api/routes/items.py', method='get', path='/items', name='list_items')
        files = result.get("files_created", []) + result.get("files_modified", [])
        for fpath in files:
            if fpath.endswith(".py"):
                full_path = Path(project_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
                source = full_path.read_text()
                import ast
                ast.parse(source)
    finally:
        shutil.rmtree(project_dir)
