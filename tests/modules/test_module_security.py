"""Tests for module: security"""

import ast
import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import Finding, Severity


class TestSecurityScaffold:
    """Tests for the security scaffold tool."""

    def test_scaffold_creates_module(self):
        """Scaffold must create a security module with expected files."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir, with_rate_limit=True)

            assert "created_files" in result
            assert "security_path" in result
            assert result["with_rate_limit"] is True
            assert result["with_csp"] is True

            created = result["created_files"]
            assert any("middleware.py" in f for f in created)
            assert any("cors.py" in f for f in created)
            assert any("exceptions.py" in f for f in created)
            assert any("rate_limit.py" in f for f in created)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_creates_valid_python(self):
        """All generated Python files must be syntactically valid."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir, with_rate_limit=True)

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text(encoding="utf-8")
                    ast.parse(source)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_idempotent(self):
        """Running scaffold twice must be safe."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result1 = generate_security_middleware(project_dir, with_rate_limit=True)
            result2 = generate_security_middleware(project_dir, with_rate_limit=True)

            assert len(result1["created_files"]) == len(result2["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_rate_limit(self):
        """Scaffold without rate limiting must not include rate_limit.py."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir, with_rate_limit=False)

            assert not any("rate_limit.py" in f for f in result["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_security_headers(self):
        """Generated middleware must include security headers."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir)

            middleware_path = Path(result["security_path"]) / "middleware.py"
            middleware_source = middleware_path.read_text()
            assert "X-Content-Type-Options" in middleware_source
            assert "X-Frame-Options" in middleware_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_csp(self):
        """Generated middleware with CSP must include Content-Security-Policy."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir, with_csp=True)

            middleware_path = Path(result["security_path"]) / "middleware.py"
            middleware_source = middleware_path.read_text()
            assert "Content-Security-Policy" in middleware_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_cors_config(self):
        """Generated CORS config must have strict origin list."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(
                project_dir,
                cors_origins=["https://myapp.com"],
            )

            cors_path = Path(result["security_path"]) / "cors.py"
            cors_source = cors_path.read_text()
            assert "myapp.com" in cors_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_exception_handlers(self):
        """Generated exception handlers must sanitize errors."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir)

            exceptions_path = Path(result["security_path"]) / "exceptions.py"
            exceptions_source = exceptions_path.read_text()
            assert "exception_handler" in exceptions_source
            assert "JSONResponse" in exceptions_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_integration_hint(self):
        """Scaffold result must include integration hint."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir)

            assert "integration_hint" in result
            assert len(result["integration_hint"]) > 0
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_findings_for_missing_rate_limit(self):
        """Scaffold without rate limit must include findings."""
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_security_middleware(project_dir, with_rate_limit=False)

            assert "findings" in result
            assert len(result["findings"]) > 0
        finally:
            shutil.rmtree(project_dir)


class TestSecurityVerify:
    """Tests for the security verify tool."""

    def test_verify_on_empty_project(self):
        """Verify must handle empty project gracefully."""
        from modules.security.tools.verify_security import verify_security_config

        project_dir = tempfile.mkdtemp()
        try:
            findings = verify_security_config(project_dir)
            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_security_headers(self):
        """Verify must detect missing security headers in middleware file."""
        from modules.security.tools.verify_security import verify_security_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from fastapi import FastAPI, Request, Response
app = FastAPI()

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    return response
'''
            (Path(project_dir) / "middleware.py").write_text(bad_code)

            findings = verify_security_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "SEC-02" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_wildcard_cors(self):
        """Verify must detect wildcard CORS origin."""
        from modules.security.tools.verify_security import verify_security_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
)
'''
            (Path(project_dir) / "main.py").write_text(bad_code)

            findings = verify_security_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "SEC-02" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_rate_limiting(self):
        """Verify must detect missing rate limiting."""
        from modules.security.tools.verify_security import verify_security_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from fastapi import FastAPI
app = FastAPI()
'''
            (Path(project_dir) / "main.py").write_text(bad_code)

            findings = verify_security_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "SEC-03" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_clean_project_no_critical(self):
        """Verify must not flag a well-implemented security module."""
        from modules.security.tools.verify_security import verify_security_config
        from modules.security.tools.scaffold_security import generate_security_middleware

        project_dir = tempfile.mkdtemp()
        try:
            generate_security_middleware(project_dir, with_rate_limit=True)
            findings = verify_security_config(project_dir)

            critical = [f for f in findings if f.severity == Severity.CRITICAL]
            assert len(critical) == 0
        finally:
            shutil.rmtree(project_dir)


class TestSecurityMCPTool:
    """Tests for MCP tool registration."""

    def test_scaffold_mcp_tool_registered(self):
        """Scaffold must have MCP_TOOL dict."""
        from modules.security.tools.scaffold_security import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_security_middleware"

    def test_verify_mcp_tool_registered(self):
        """Verify must have MCP_TOOL dict."""
        from modules.security.tools.verify_security import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "verify_security_config"

    def test_pentest_mcp_tool_registered(self):
        """Pentest tool must have MCP_TOOL dict."""
        from modules.security.tools.pentest_api import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "mcp_fastapi_pentest"
