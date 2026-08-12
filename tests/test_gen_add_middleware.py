"""Tests for generator: add_middleware (tool)."""

import pytest
import tempfile
import shutil
from pathlib import Path


def test_generator_add_middleware_creates_files():
    """Tool must run successfully and return files_created or notes."""
    from generators.tools.add_middleware import add_middleware

    project_dir = tempfile.mkdtemp()
    try:
        result = add_middleware(project_dir=project_dir, name='TestMiddleware', code='class TestMiddleware:\\n    pass\\n', position='before_logging')
        assert isinstance(result, dict)
        assert "notes" in result
    finally:
        shutil.rmtree(project_dir)


def test_generator_add_middleware_idempotent():
    """Running twice must be safe — second run skips or succeeds."""
    from generators.tools.add_middleware import add_middleware

    project_dir = tempfile.mkdtemp()
    try:
        result1 = add_middleware(project_dir=project_dir, name='TestMiddleware', code='class TestMiddleware:\\n    pass\\n', position='before_logging')
        result2 = add_middleware(project_dir=project_dir, name='TestMiddleware', code='class TestMiddleware:\\n    pass\\n', position='before_logging')
        assert isinstance(result2, dict)
    finally:
        shutil.rmtree(project_dir)


def test_generator_add_middleware_valid_python():
    """Generated .py files must pass ast.parse."""
    from generators.tools.add_middleware import add_middleware

    project_dir = tempfile.mkdtemp()
    try:
        result = add_middleware(project_dir=project_dir, name='TestMiddleware', code='class TestMiddleware:\\n    pass\\n', position='before_logging')
        files = result.get("files_created", []) + result.get("files_modified", [])
        for fpath in files:
            if fpath.endswith(".py"):
                full_path = Path(project_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
                source = full_path.read_text()
                import ast
                ast.parse(source)
    finally:
        shutil.rmtree(project_dir)
