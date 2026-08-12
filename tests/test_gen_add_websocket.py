"""Tests for generator: add_websocket (tool)."""

import pytest
import tempfile
import shutil
from pathlib import Path


def test_generator_add_websocket_creates_files():
    """Tool must run successfully and return files_created or notes."""
    from generators.tools.add_websocket import add_websocket

    project_dir = tempfile.mkdtemp()
    try:
        result = add_websocket(project_dir=project_dir, name='notifications', path='/ws/notifications')
        assert isinstance(result, dict)
        assert "notes" in result
    finally:
        shutil.rmtree(project_dir)


def test_generator_add_websocket_idempotent():
    """Running twice must be safe — second run skips or succeeds."""
    from generators.tools.add_websocket import add_websocket

    project_dir = tempfile.mkdtemp()
    try:
        result1 = add_websocket(project_dir=project_dir, name='notifications', path='/ws/notifications')
        result2 = add_websocket(project_dir=project_dir, name='notifications', path='/ws/notifications')
        assert isinstance(result2, dict)
    finally:
        shutil.rmtree(project_dir)


def test_generator_add_websocket_valid_python():
    """Generated .py files must pass ast.parse."""
    from generators.tools.add_websocket import add_websocket

    project_dir = tempfile.mkdtemp()
    try:
        result = add_websocket(project_dir=project_dir, name='notifications', path='/ws/notifications')
        files = result.get("files_created", []) + result.get("files_modified", [])
        for fpath in files:
            if fpath.endswith(".py"):
                full_path = Path(project_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
                source = full_path.read_text()
                import ast
                ast.parse(source)
    finally:
        shutil.rmtree(project_dir)
