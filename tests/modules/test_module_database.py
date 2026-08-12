"""Tests for module: database"""

import ast
import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import Finding, Severity


class TestDatabaseScaffold:
    """Tests for the database scaffold tool."""

    def test_scaffold_creates_module(self):
        """Scaffold must create a database module with expected files."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
                with_alembic=True,
                with_multi_tenancy=False,
            )

            assert "created_files" in result
            assert "db_path" in result
            assert result["with_alembic"] is True
            assert result["with_multi_tenancy"] is False

            created = result["created_files"]
            assert any("engine.py" in f for f in created)
            assert any("base.py" in f for f in created)
            assert any("alembic" in f for f in created)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_creates_valid_python(self):
        """All generated Python files must be syntactically valid."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
                with_alembic=True,
            )

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text(encoding="utf-8")
                    ast.parse(source)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_idempotent(self):
        """Running scaffold twice must be safe."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            result1 = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            )
            result2 = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            )

            assert len(result1["created_files"]) == len(result2["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_sync_driver_raises(self):
        """Scaffold with sync PostgreSQL driver must raise ValueError."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            with pytest.raises(ValueError, match="asyncpg"):
                generate_db_module(
                    project_dir,
                    db_url="postgresql://user:pass@localhost:5432/db",
                )
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_alembic(self):
        """Scaffold without alembic must not include alembic files."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
                with_alembic=False,
            )

            assert not any("alembic" in f for f in result["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_with_multi_tenancy(self):
        """Scaffold with multi-tenancy must include tenancy.py."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
                with_multi_tenancy=True,
            )

            assert any("tenancy.py" in f for f in result["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_pool_config(self):
        """Generated engine.py must include pool configuration."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            )

            engine_path = Path(result["db_path"]) / "engine.py"
            engine_source = engine_path.read_text()
            assert "pool_size" in engine_source
            assert "max_overflow" in engine_source
            assert "pool_pre_ping" in engine_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_timestamp_mixin(self):
        """Generated base.py must include TimestampMixin."""
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            )

            base_path = Path(result["db_path"]) / "models" / "base.py"
            base_source = base_path.read_text()
            assert "TimestampMixin" in base_source
            assert "SoftDeleteMixin" in base_source
        finally:
            shutil.rmtree(project_dir)


class TestDatabaseVerify:
    """Tests for the database verify tool."""

    def test_verify_on_empty_project(self):
        """Verify must handle empty project gracefully."""
        from modules.database.tools.verify_db import verify_db_config

        project_dir = tempfile.mkdtemp()
        try:
            findings = verify_db_config(project_dir)
            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_pool_pre_ping(self):
        """Verify must detect missing pool_pre_ping."""
        from modules.database.tools.verify_db import verify_db_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from sqlalchemy.ext.asyncio import create_async_engine
engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/db")
'''
            (Path(project_dir) / "database.py").write_text(bad_code)

            findings = verify_db_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "DB-01" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_expire_on_commit(self):
        """Verify must detect missing expire_on_commit=False."""
        from modules.database.tools.verify_db import verify_db_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from sqlalchemy.ext.asyncio import async_sessionmaker
factory = async_sessionmaker(engine)
'''
            (Path(project_dir) / "database.py").write_text(bad_code)

            findings = verify_db_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "DB-03" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_sync_driver(self):
        """Verify must detect sync database driver in async context."""
        from modules.database.tools.verify_db import verify_db_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from sqlalchemy import create_engine
from fastapi import FastAPI

app = FastAPI()
engine = create_engine("postgresql://user:pass@localhost/db")

async def get_data():
    pass
'''
            (Path(project_dir) / "database.py").write_text(bad_code)

            findings = verify_db_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "DB-06" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_clean_project_no_critical(self):
        """Verify must not flag a well-implemented database module."""
        from modules.database.tools.verify_db import verify_db_config
        from modules.database.tools.scaffold_db import generate_db_module

        project_dir = tempfile.mkdtemp()
        try:
            generate_db_module(
                project_dir,
                db_url="postgresql+asyncpg://user:pass@localhost:5432/db",
            )
            findings = verify_db_config(project_dir)

            critical = [f for f in findings if f.severity == Severity.CRITICAL]
            assert len(critical) == 0
        finally:
            shutil.rmtree(project_dir)


class TestDatabaseOperate:
    """Tests for the database operate tool."""

    def test_check_pool_health_returns_dict(self):
        """check_pool_health must return a dict with expected keys."""
        from modules.database.tools.operate_db import check_pool_health

        try:
            result = check_pool_health("postgresql://localhost:5432/nonexistent")
            assert isinstance(result, dict)
            assert "status" in result
            assert result["status"] in ("healthy", "degraded", "error", "unreachable")
        except ImportError:
            pytest.skip("psycopg/psycopg2 not installed")

    def test_check_pool_health_unreachable(self):
        """check_pool_health must handle unreachable DB gracefully."""
        from modules.database.tools.operate_db import check_pool_health

        try:
            result = check_pool_health("postgresql://localhost:9999/nonexistent")
            assert result["status"] in ("unreachable", "error")
        except ImportError:
            pytest.skip("psycopg/psycopg2 not installed")


class TestDatabaseMCPTool:
    """Tests for MCP tool registration."""

    def test_scaffold_mcp_tool_registered(self):
        """Scaffold must have MCP_TOOL dict."""
        from modules.database.tools.scaffold_db import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_db_module"

    def test_verify_mcp_tool_registered(self):
        """Verify must have MCP_TOOL dict."""
        from modules.database.tools.verify_db import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "verify_db_config"
