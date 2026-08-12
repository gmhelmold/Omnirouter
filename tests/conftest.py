"""
Pytest configuration and fixtures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gateway.config import GatewayConfig


@pytest.fixture
def mock_config():
    """Mock gateway config for testing."""
    config = MagicMock(spec=GatewayConfig)
    config.openrouter_api_key = "test-or-key"
    config.openrouter_base_url = "https://openrouter.ai/api"
    config.groq_api_key = "test-groq-key"
    config.gemini_api_key = "test-gemini-key"
    config.nim_base_url = "http://localhost:8000"
    config.nim_api_key = "test-nim-key"
    config.mistral_api_key = "test-mistral-key"
    config.opencode_serve_url = "http://127.0.0.1:5051"
    config.opencode_agent = "general"
    config.openrouter_model_map = {"opus-5": "anthropic/claude-opus-5"}
    config.groq_model_map = {"llama3": "llama-3.3-70b-versatile"}
    config.gemini_model_map = {"flash": "gemini-1.5-flash"}
    config.nim_model_map = {"llama3": "meta/llama-3.1-70b-instruct"}
    config.mistral_model_map = {"large": "mistral-large-latest"}
    config.opencode_model_map = {"big-pickle": "big-pickle"}
    config.fallback_chains = {
        "groq": ["groq", "openrouter-groq"],
        "gemini": ["gemini"],
    }
    config.max_fallback_attempts = 3
    config.request_timeout = 600.0
    config.connect_timeout = 10.0
    return config


@pytest.fixture
def mock_httpx_response():
    """Create a mock httpx response."""
    def _make(status_code: int = 200, json_data: dict | None = None, text: str = ""):
        response = MagicMock(spec=httpx.Response)
        response.status_code = status_code
        response.json = AsyncMock(return_value=json_data or {})
        response.text = text
        response.aread = AsyncMock(return_value=b"test")
        return response
    return _make


@pytest.fixture
def sample_anthropic_messages():
    """Sample Anthropic format messages."""
    return [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello, world!"},
    ]


@pytest.fixture
def sample_anthropic_tools():
    """Sample Anthropic format tools."""
    return [
        {
            "name": "read_file",
            "description": "Read a file from disk",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"}
                },
                "required": ["path"],
            },
        }
    ]


@pytest.fixture
def mock_httpx_client():
    """Mock httpx AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client
