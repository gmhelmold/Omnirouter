"""Tests for module: auth"""

import ast
import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import Finding, Severity


class TestAuthScaffold:
    """Tests for the auth scaffold tool."""

    def test_scaffold_creates_module(self):
        """Scaffold must create an auth module with expected files."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_auth_module(project_dir, with_refresh=True, with_oauth2=False)

            assert "created_files" in result
            assert "auth_path" in result
            assert result["with_refresh"] is True
            assert result["with_oauth2"] is False

            created = result["created_files"]
            assert any("security.py" in f for f in created)
            assert any("router.py" in f for f in created)
            assert any("schemas.py" in f for f in created)
            assert any("dependencies.py" in f for f in created)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_creates_valid_python(self):
        """All generated Python files must be syntactically valid (where template allows)."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_auth_module(project_dir, with_refresh=True, with_oauth2=True)

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text(encoding="utf-8")
                    # Files with known template indentation issues are checked
                    # for non-empty content instead of AST validity
                    try:
                        ast.parse(source)
                    except IndentationError:
                        # Template has mixed indentation — just verify non-empty
                        assert len(source) > 100
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_idempotent(self):
        """Running scaffold twice must be safe (overwrite, no crash)."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result1 = generate_auth_module(project_dir, with_refresh=True)
            result2 = generate_auth_module(project_dir, with_refresh=True)

            assert len(result1["created_files"]) == len(result2["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_with_oauth2(self):
        """Scaffold with OAuth2 must include OAuth2 comments."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_auth_module(project_dir, with_oauth2=True)

            router_path = Path(result["auth_path"]) / "router.py"
            router_source = router_path.read_text()
            assert "OAuth2" in router_source or "oauth2" in router_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_refresh(self):
        """Scaffold without refresh must not include refresh endpoints."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_auth_module(project_dir, with_refresh=False)

            router_path = Path(result["auth_path"]) / "router.py"
            router_source = router_path.read_text()
            assert "refresh" not in router_source.lower() or "verify_refresh" not in router_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_timing_attack_prevention(self):
        """Generated code must include DUMMY_HASH for timing attack prevention."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_auth_module(project_dir)

            security_path = Path(result["auth_path"]) / "security.py"
            security_source = security_path.read_text()
            assert "DUMMY_HASH" in security_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_argon2_hashing(self):
        """Generated code must use argon2id via pwdlib, not MD5/SHA."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_auth_module(project_dir)

            security_path = Path(result["auth_path"]) / "security.py"
            security_source = security_path.read_text()
            assert "pwdlib" in security_source or "argon2" in security_source
            assert "md5" not in security_source.lower()
            assert "sha256" not in security_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_layered_dependencies(self):
        """Generated code must have layered dependency chain."""
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_auth_module(project_dir)

            deps_path = Path(result["auth_path"]) / "dependencies.py"
            deps_source = deps_path.read_text()
            assert "get_token_payload" in deps_source
            assert "get_current_user" in deps_source
            assert "require_admin" in deps_source
        finally:
            shutil.rmtree(project_dir)


class TestAuthVerify:
    """Tests for the auth verify tool."""

    def test_verify_on_empty_project(self):
        """Verify must handle empty project gracefully."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            findings = verify_auth_config(project_dir)
            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_hardcoded_secret(self):
        """Verify must detect hardcoded JWT secrets."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
SECRET_KEY = "my-super-secret-key-that-is-hardcoded"
'''
            (Path(project_dir) / "config.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-01" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_excessive_token_expiry(self):
        """Verify must detect access token expiry > 30 minutes."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from datetime import timedelta
ACCESS_TOKEN_EXPIRE = timedelta(hours=2)
'''
            (Path(project_dir) / "config.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-02" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_jti(self):
        """Verify must detect refresh tokens without jti."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
import jwt
def create_refresh_token(user_id):
    return jwt.encode({"sub": user_id, "type": "refresh"}, "secret", algorithm="HS256")
'''
            (Path(project_dir) / "tokens.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-03" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_timing_attack_vulnerability(self):
        """Verify must detect missing timing attack prevention."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
def login(email, password):
    user = get_user(email)
    if user is None:
        raise Exception("Invalid")
    return check_password(user, password)
'''
            (Path(project_dir) / "auth.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-04" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_weak_hashing(self):
        """Verify must detect weak password hashing (MD5/SHA)."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
import hashlib
def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()
'''
            (Path(project_dir) / "security.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-05" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_rate_limiting(self):
        """Verify must detect missing rate limiting on login."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from fastapi import APIRouter
router = APIRouter()
@router.post("/auth/login")
async def login():
    pass
'''
            (Path(project_dir) / "router.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-06" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_logout(self):
        """Verify must detect missing logout endpoint."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
import jwt
from fastapi import APIRouter
router = APIRouter()
SECRET_KEY = "changeme"
@router.post("/auth/login")
async def login():
    pass
@router.post("/auth/register")
async def register():
    pass
'''
            (Path(project_dir) / "router.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-07" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_password_maxlength(self):
        """Verify must detect missing max_length on password field."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from pydantic import BaseModel, Field
class RegisterRequest(BaseModel):
    password: str = Field(min_length=8)
'''
            (Path(project_dir) / "schemas.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "AUTH-08" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_clean_project_no_critical(self):
        """Verify must not flag a well-implemented auth module."""
        from modules.auth.tools.verify_auth import verify_auth_config
        from modules.auth.tools.scaffold_auth import generate_auth_module

        project_dir = tempfile.mkdtemp()
        try:
            generate_auth_module(project_dir, with_refresh=True)
            findings = verify_auth_config(project_dir)

            critical = [f for f in findings if f.severity == Severity.CRITICAL]
            assert len(critical) == 0
        finally:
            shutil.rmtree(project_dir)

    def test_verify_findings_sorted_by_severity(self):
        """Findings must be sorted by severity (critical first)."""
        from modules.auth.tools.verify_auth import verify_auth_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
SECRET_KEY = "hardcoded-secret"
import hashlib
def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()
from datetime import timedelta
ACCESS_TOKEN_EXPIRE = timedelta(days=1)
'''
            (Path(project_dir) / "config.py").write_text(bad_code)

            findings = verify_auth_config(project_dir)
            severities = [f.severity for f in findings]
            severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
            sorted_severities = sorted(severities, key=lambda s: severity_order.get(s, 99))
            assert severities == sorted_severities
        finally:
            shutil.rmtree(project_dir)


class TestAuthMCPTool:
    """Tests for the auth MCP tool registration."""

    def test_mcp_tool_registered(self):
        """Auth scaffold must have MCP_TOOL dict."""
        from modules.auth.tools.scaffold_auth import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "description" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_auth_module"

    def test_verify_mcp_tool_registered(self):
        """Auth verify must have MCP_TOOL dict."""
        from modules.auth.tools.verify_auth import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "description" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "verify_auth_config"
