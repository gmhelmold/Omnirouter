"""Tests for module: caching"""

import ast
import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import Finding, Severity


class TestCachingScaffold:
    """Tests for the caching scaffold tool."""

    def test_scaffold_creates_module(self):
        """Scaffold must create a cache module with expected files."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_cache_module(project_dir, with_stampede_prevention=True)

            assert "created_files" in result
            assert "cache_path" in result
            assert result["backend"] == "redis"
            assert result["with_stampede_prevention"] is True

            created = result["created_files"]
            assert any("client.py" in f for f in created)
            assert any("aside.py" in f for f in created)
            assert any("keys.py" in f for f in created)
            assert any("lock.py" in f for f in created)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_creates_valid_python(self):
        """All generated Python files must be syntactically valid."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_cache_module(project_dir, with_stampede_prevention=True)

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text(encoding="utf-8")
                    ast.parse(source)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_idempotent(self):
        """Running scaffold twice must be safe."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result1 = generate_cache_module(project_dir)
            result2 = generate_cache_module(project_dir)

            assert len(result1["created_files"]) == len(result2["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_invalid_backend_raises(self):
        """Scaffold with unsupported backend must raise ValueError."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            with pytest.raises(ValueError, match="Unsupported backend"):
                generate_cache_module(project_dir, backend="memcached")
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_stampede_prevention(self):
        """Scaffold without stampede prevention must have placeholder stampede.py."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_cache_module(project_dir, with_stampede_prevention=False)

            stampede_path = Path(result["cache_path"]) / "stampede.py"
            stampede_source = stampede_path.read_text()
            assert "disabled" in stampede_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_cache_aside(self):
        """Generated code must include cache-aside pattern."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_cache_module(project_dir)

            aside_path = Path(result["cache_path"]) / "aside.py"
            aside_source = aside_path.read_text()
            assert "CacheAside" in aside_source
            assert "get_or_set" in aside_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_distributed_lock(self):
        """Generated code must include distributed lock implementation."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_cache_module(project_dir)

            lock_path = Path(result["cache_path"]) / "lock.py"
            lock_source = lock_path.read_text()
            assert "RedisLock" in lock_source
            assert "LockNotAcquired" in lock_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_ttl_strategy(self):
        """Generated code must include TTL strategy with jitter."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_cache_module(project_dir)

            keys_path = Path(result["cache_path"]) / "keys.py"
            keys_source = keys_path.read_text()
            assert "CacheTTL" in keys_source
            assert "ttl_with_jitter" in keys_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_stampede_prevention(self):
        """Generated code with stampede prevention must include XFetch."""
        from modules.caching.tools.scaffold_cache import generate_cache_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_cache_module(project_dir, with_stampede_prevention=True)

            stampede_path = Path(result["cache_path"]) / "stampede.py"
            stampede_source = stampede_path.read_text()
            assert "xfetch" in stampede_source.lower()
        finally:
            shutil.rmtree(project_dir)


class TestCachingOperate:
    """Tests for the caching operate tool."""

    def test_check_cache_health_returns_dict(self):
        """check_cache_health must return a dict with expected keys."""
        from modules.caching.tools.operate_cache import check_cache_health

        result = check_cache_health("redis://localhost:6379")

        assert isinstance(result, dict)
        assert "status" in result
        assert result["status"] in ("healthy", "degraded", "unhealthy", "unreachable", "error")

    def test_check_cache_health_unreachable(self):
        """check_cache_health must handle unreachable Redis gracefully."""
        from modules.caching.tools.operate_cache import check_cache_health

        result = check_cache_health("redis://localhost:9999")

        assert result["status"] in ("unreachable", "error")
        assert "error" in result or "findings" in result

    def test_check_cache_health_has_findings(self):
        """check_cache_health must include findings list."""
        from modules.caching.tools.operate_cache import check_cache_health

        result = check_cache_health("redis://localhost:6379")

        assert "findings" in result
        assert isinstance(result["findings"], list)


class TestCachingMCPTool:
    """Tests for MCP tool registration."""

    def test_scaffold_mcp_tool_registered(self):
        """Scaffold must have MCP_TOOL dict."""
        from modules.caching.tools.scaffold_cache import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_cache_module"

    def test_operate_mcp_tool_registered(self):
        """Operate must have MCP_TOOL dict."""
        from modules.caching.tools.operate_cache import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "check_cache_health"
