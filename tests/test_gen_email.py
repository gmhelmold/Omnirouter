"""Tests for generator: email."""

import pytest
import tempfile
import shutil
from pathlib import Path


def test_generator_email_creates_files():
    """Generator must run successfully and create files."""
    from generators.infra.email import generate_email_utils

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_email_utils(output_dir, password_reset_expiry_minutes=None)
        assert isinstance(result, dict)
        assert "files_created" in result
        assert isinstance(result["files_created"], list)
        assert len(result["files_created"]) > 0, "Generator created no files"
    finally:
        shutil.rmtree(output_dir)


def test_generator_email_idempotent():
    """Running twice must be safe — second run succeeds or is no-op."""
    from generators.infra.email import generate_email_utils

    output_dir = tempfile.mkdtemp()
    try:
        result1 = generate_email_utils(output_dir, password_reset_expiry_minutes=None)
        result2 = generate_email_utils(output_dir, password_reset_expiry_minutes=None)
        assert isinstance(result2, dict)
    finally:
        shutil.rmtree(output_dir)


def test_generator_email_valid_python():
    """Generated .py files must pass ast.parse."""
    from generators.infra.email import generate_email_utils

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_email_utils(output_dir, password_reset_expiry_minutes=None)
        for fpath in result.get("files_created", []):
            if fpath.endswith(".py"):
                full_path = Path(output_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
                source = full_path.read_text()
                import ast
                ast.parse(source)
    finally:
        shutil.rmtree(output_dir)


def test_generator_email_files_exist():
    """All files_created must exist on disk after generation."""
    from generators.infra.email import generate_email_utils

    output_dir = tempfile.mkdtemp()
    try:
        result = generate_email_utils(output_dir, password_reset_expiry_minutes=None)
        for fpath in result.get("files_created", []):
            full_path = Path(output_dir) / fpath if not Path(fpath).is_absolute() else Path(fpath)
            assert full_path.exists(), f"File not found: {fpath}"
    finally:
        shutil.rmtree(output_dir)
