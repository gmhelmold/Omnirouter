"""Tests for module: websockets"""

import ast
import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import Finding, Severity


class TestWebsocketsScaffold:
    """Tests for the websockets scaffold tool."""

    def test_scaffold_creates_module(self):
        """Scaffold must create a ws module with expected files."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir, with_rooms=True, with_auth=True)

            assert "created_files" in result
            assert "ws_path" in result
            assert result["with_rooms"] is True
            assert result["with_auth"] is True

            created = result["created_files"]
            assert any("manager.py" in f for f in created)
            assert any("endpoint.py" in f for f in created)
            assert any("schemas.py" in f for f in created)
            assert any("auth.py" in f for f in created)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_creates_valid_python(self):
        """All generated Python files must be syntactically valid."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir, with_rooms=True, with_auth=True)

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text(encoding="utf-8")
                    ast.parse(source)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_idempotent(self):
        """Running scaffold twice must be safe."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result1 = generate_websocket_module(project_dir, with_rooms=True, with_auth=True)
            result2 = generate_websocket_module(project_dir, with_rooms=True, with_auth=True)

            assert len(result1["created_files"]) == len(result2["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_rooms(self):
        """Scaffold without rooms must not include room methods."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir, with_rooms=False)

            manager_path = Path(result["ws_path"]) / "manager.py"
            manager_source = manager_path.read_text()
            assert "broadcast_to_room" not in manager_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_auth(self):
        """Scaffold without auth must have no-auth auth.py."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir, with_auth=False)

            auth_path = Path(result["ws_path"]) / "auth.py"
            auth_source = auth_path.read_text()
            assert "anonymous" in auth_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_connection_manager(self):
        """Generated code must include ConnectionManager class."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir)

            manager_path = Path(result["ws_path"]) / "manager.py"
            manager_source = manager_path.read_text()
            assert "ConnectionManager" in manager_source
            assert "can_connect" in manager_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_heartbeat(self):
        """Generated endpoint must include heartbeat/ping-pong."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir)

            endpoint_path = Path(result["ws_path"]) / "endpoint.py"
            endpoint_source = endpoint_path.read_text()
            assert "heartbeat" in endpoint_source.lower()
            assert "ping" in endpoint_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_message_validation(self):
        """Generated code must validate messages against schema."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir)

            schemas_path = Path(result["ws_path"]) / "schemas.py"
            schemas_source = schemas_path.read_text()
            assert "WSIncoming" in schemas_source
            assert "WSOutgoing" in schemas_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_connection_limits(self):
        """Generated code must include connection limits."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir)

            manager_path = Path(result["ws_path"]) / "manager.py"
            manager_source = manager_path.read_text()
            assert "MAX_PER_USER" in manager_source
            assert "MAX_PER_IP" in manager_source
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_redis_pubsub(self):
        """Generated code must include Redis pub/sub for multi-worker."""
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_websocket_module(project_dir)

            pubsub_path = Path(result["ws_path"]) / "redis_pubsub.py"
            assert pubsub_path.exists()
        finally:
            shutil.rmtree(project_dir)


class TestWebsocketsVerify:
    """Tests for the websockets verify tool."""

    def test_verify_on_empty_project(self):
        """Verify must handle empty project gracefully."""
        from modules.websockets.tools.verify_ws import verify_websocket_config

        project_dir = tempfile.mkdtemp()
        try:
            findings = verify_websocket_config(project_dir)
            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_auth(self):
        """Verify must detect missing WebSocket authentication."""
        from modules.websockets.tools.verify_ws import verify_websocket_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from fastapi import WebSocket
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
'''
            (Path(project_dir) / "ws.py").write_text(bad_code)

            findings = verify_websocket_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "WS-01" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_heartbeat(self):
        """Verify must detect missing heartbeat/ping-pong."""
        from modules.websockets.tools.verify_ws import verify_websocket_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from fastapi import WebSocket
import jwt
async def websocket_endpoint(websocket: WebSocket, token: str):
    payload = jwt.decode(token, "secret", algorithms=["HS256"])
    await websocket.accept()
'''
            (Path(project_dir) / "ws.py").write_text(bad_code)

            findings = verify_websocket_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "WS-02" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_connection_limits(self):
        """Verify must detect missing connection limits."""
        from modules.websockets.tools.verify_ws import verify_websocket_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
from fastapi import WebSocket
import jwt
async def websocket_endpoint(websocket: WebSocket, token: str):
    payload = jwt.decode(token, "secret", algorithms=["HS256"])
    await websocket.accept()
'''
            (Path(project_dir) / "ws.py").write_text(bad_code)

            findings = verify_websocket_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "WS-03" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_clean_project_returns_list(self):
        """Verify must return a list when run on scaffolded WebSocket module."""
        from modules.websockets.tools.verify_ws import verify_websocket_config
        from modules.websockets.tools.scaffold_ws import generate_websocket_module

        project_dir = tempfile.mkdtemp()
        try:
            generate_websocket_module(project_dir, with_rooms=True, with_auth=True)
            findings = verify_websocket_config(project_dir)

            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)


class TestWebsocketsMCPTool:
    """Tests for MCP tool registration."""

    def test_scaffold_mcp_tool_registered(self):
        """Scaffold must have MCP_TOOL dict."""
        from modules.websockets.tools.scaffold_ws import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_websocket_module"

    def test_verify_mcp_tool_registered(self):
        """Verify must have MCP_TOOL dict."""
        from modules.websockets.tools.verify_ws import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "verify_websocket_config"
