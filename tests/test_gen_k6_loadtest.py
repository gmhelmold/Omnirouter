"""Tests for generator: k6_loadtest."""

import pytest
import tempfile
import shutil
from pathlib import Path


def test_generator_k6_loadtest_creates_files():
    """Generator must run successfully and create files."""
    from generators.deployment.k6_loadtest import generate_k6_loadtest

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_k6_loadtest(output_dir, endpoints=None, preset='smoke', base_url='http://localhost:8000')
        assert isinstance(result, dict)
        assert "files_created" in result
        assert isinstance(result["files_created"], list)
        assert len(result["files_created"]) > 0, "Generator created no files"
    finally:
        shutil.rmtree(output_dir)


def test_generator_k6_loadtest_idempotent():
    """Running twice must be safe — second run succeeds or is no-op."""
    from generators.deployment.k6_loadtest import generate_k6_loadtest

    output_dir = tempfile.mkdtemp()
    try:
        result1 = generate_k6_loadtest(output_dir, endpoints=None, preset='smoke', base_url='http://localhost:8000')
        result2 = generate_k6_loadtest(output_dir, endpoints=None, preset='smoke', base_url='http://localhost:8000')
        assert isinstance(result2, dict)
    finally:
        shutil.rmtree(output_dir)


def test_generator_k6_loadtest_valid_python():
    """Generated .py files must pass ast.parse."""
    from generators.deployment.k6_loadtest import generate_k6_loadtest

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_k6_loadtest(output_dir, endpoints=None, preset='smoke', base_url='http://localhost:8000')
        for fpath in result.get("files_created", []):
            if fpath.endswith(".py"):
                full_path = Path(output_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
                source = full_path.read_text()
                import ast
                ast.parse(source)
    finally:
        shutil.rmtree(output_dir)


def test_generator_k6_loadtest_files_exist():
    """All files_created must exist on disk after generation."""
    from generators.deployment.k6_loadtest import generate_k6_loadtest

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_k6_loadtest(output_dir, endpoints=None, preset='smoke', base_url='http://localhost:8000')
        for fpath in result.get("files_created", []):
            full_path = Path(output_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
            assert full_path.exists(), f"File not found: {fpath}"
    finally:
        shutil.rmtree(output_dir)
