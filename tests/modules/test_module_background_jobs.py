"""Tests for module: background_jobs"""

import ast
import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import Finding, Severity


class TestBackgroundJobsScaffold:
    """Tests for the background jobs scaffold tool."""

    def test_scaffold_creates_module(self):
        """Scaffold must create a jobs module with expected files."""
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_background_jobs(project_dir, backend="arq", with_dlq=True)

            assert "created_files" in result
            assert "jobs_path" in result
            assert result["backend"] == "arq"
            assert result["with_dlq"] is True

            created = result["created_files"]
            assert any("tasks.py" in f for f in created)
            assert any("worker.py" in f for f in created)
            assert any("health.py" in f for f in created)
            assert any("dlq.py" in f for f in created)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_creates_valid_python(self):
        """All generated Python files must be syntactically valid."""
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_background_jobs(project_dir, with_dlq=True)

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text(encoding="utf-8")
                    ast.parse(source)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_idempotent(self):
        """Running scaffold twice must be safe."""
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            result1 = generate_background_jobs(project_dir, with_dlq=True)
            result2 = generate_background_jobs(project_dir, with_dlq=True)

            assert len(result1["created_files"]) == len(result2["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_dlq(self):
        """Scaffold without DLQ must not include dlq.py."""
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_background_jobs(project_dir, with_dlq=False)

            assert not any("dlq.py" in f for f in result["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_invalid_backend_raises(self):
        """Scaffold with unsupported backend must raise ValueError."""
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            with pytest.raises(ValueError, match="Unsupported backend"):
                generate_background_jobs(project_dir, backend="celery")
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_retry_config(self):
        """Generated code must include retry configuration."""
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_background_jobs(project_dir)

            tasks_path = Path(result["jobs_path"]) / "tasks.py"
            tasks_source = tasks_path.read_text()
            assert "retry" in tasks_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_health_monitoring(self):
        """Generated code must include health monitoring."""
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_background_jobs(project_dir)

            health_path = Path(result["jobs_path"]) / "health.py"
            assert health_path.exists()
        finally:
            shutil.rmtree(project_dir)


class TestBackgroundJobsVerify:
    """Tests for the background jobs verify tool."""

    def test_verify_on_empty_project(self):
        """Verify must handle empty project gracefully."""
        from modules.background_jobs.tools.verify_jobs import verify_job_config

        project_dir = tempfile.mkdtemp()
        try:
            findings = verify_job_config(project_dir)
            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_retry_config(self):
        """Verify must detect missing retry configuration."""
        from modules.background_jobs.tools.verify_jobs import verify_job_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
async def process_order(ctx, order_id):
    pass
'''
            (Path(project_dir) / "tasks.py").write_text(bad_code)

            findings = verify_job_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "JOBS-01" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_dlq(self):
        """Verify must detect missing dead letter queue."""
        from modules.background_jobs.tools.verify_jobs import verify_job_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from arq import cron
async def process_order(ctx, order_id):
    pass
async def send_notification(ctx, user_id, message):
    pass
'''
            (Path(project_dir) / "tasks.py").write_text(bad_code)

            findings = verify_job_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "JOBS-02" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_idempotency(self):
        """Verify must detect missing idempotency keys."""
        from modules.background_jobs.tools.verify_jobs import verify_job_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
async def process_order(ctx, order_id):
    pass
'''
            (Path(project_dir) / "tasks.py").write_text(bad_code)

            findings = verify_job_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "JOBS-03" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_timeout(self):
        """Verify must detect missing job timeout configuration."""
        from modules.background_jobs.tools.verify_jobs import verify_job_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from arq import WorkerSettings
my_settings = WorkerSettings()

async def process_order(ctx, order_id):
    pass
'''
            (Path(project_dir) / "tasks.py").write_text(bad_code)

            findings = verify_job_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "JOBS-04" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_health_monitoring(self):
        """Verify must detect missing queue health monitoring."""
        from modules.background_jobs.tools.verify_jobs import verify_job_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from arq import cron
async def process_order(order_id):
    pass
'''
            (Path(project_dir) / "tasks.py").write_text(bad_code)

            findings = verify_job_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "JOBS-06" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_clean_project_no_critical(self):
        """Verify must not flag a well-implemented jobs module."""
        from modules.background_jobs.tools.verify_jobs import verify_job_config
        from modules.background_jobs.tools.scaffold_jobs import generate_background_jobs

        project_dir = tempfile.mkdtemp()
        try:
            generate_background_jobs(project_dir, with_dlq=True)
            findings = verify_job_config(project_dir)

            critical = [f for f in findings if f.severity == Severity.CRITICAL]
            assert len(critical) == 0
        finally:
            shutil.rmtree(project_dir)


class TestBackgroundJobsMCPTool:
    """Tests for MCP tool registration."""

    def test_scaffold_mcp_tool_registered(self):
        """Scaffold must have MCP_TOOL dict."""
        from modules.background_jobs.tools.scaffold_jobs import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_background_jobs"

    def test_verify_mcp_tool_registered(self):
        """Verify must have MCP_TOOL dict."""
        from modules.background_jobs.tools.verify_jobs import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "verify_job_config"
