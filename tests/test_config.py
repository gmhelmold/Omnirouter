"""
Tests for config module.
"""

from __future__ import annotations

import pytest
from pathlib import Path

import yaml

from gateway.config import (
    GatewayConfig,
    GatewayYamlConfig,
    OpenRouterModelMap,
    GroqModelMap,
    GeminiModelMap,
    CerebrasModelMap,
    OpencodeBridgeModelMap,
    FallbackChains,
)


class TestGatewayYamlConfig:
    def test_default_values(self):
        config = GatewayYamlConfig()
        assert isinstance(config.openrouter_model_map, OpenRouterModelMap)
        assert isinstance(config.groq_model_map, GroqModelMap)
        assert isinstance(config.gemini_model_map, GeminiModelMap)
        assert isinstance(config.cerebras_model_map, CerebrasModelMap)
        assert isinstance(config.opencode_bridge_model_map, OpencodeBridgeModelMap)
        assert isinstance(config.fallback_chains, FallbackChains)

    def test_cerebras_model_map_default(self):
        config = GatewayYamlConfig()
        assert config.cerebras_model_map.gpt_oss == "gpt-oss-120b"
        assert config.cerebras_model_map.glm == "zai-glm-4.7"

    def test_opencode_bridge_endpoints_default(self):
        config = GatewayYamlConfig()
        endpoints = config.opencode_bridge_endpoints
        assert "groq" in endpoints
        assert "gemini" in endpoints
        assert "mistral" in endpoints

    def test_fallback_chains_default(self):
        config = GatewayYamlConfig()
        chains = config.fallback_chains
        assert "groq" in chains.groq
        assert "gemini" in chains.gemini
        assert "mistral" in chains.mistral
        assert "cerebras" in chains.cerebras


class TestGatewayConfig:
    def test_env_loading(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_PORT", "9999")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        config = GatewayConfig()
        assert config.port == 9999
        assert config.openrouter_api_key == "test-key"

    def test_default_values(self):
        config = GatewayConfig()
        assert config.port == 8787
        assert config.host == "127.0.0.1"
        assert config.log_level == "INFO"
        assert config.request_timeout == 600.0
        assert config.gemini_rpm == 15
        assert config.mistral_rpm == 500
        assert config.cerebras_rpm == 5

    def test_yaml_loading(self, tmp_path):
        yaml_content = """
openrouter_model_map:
  opus-5: "anthropic/claude-opus-5"
groq_model_map:
  llama3: "llama-3.3-70b-versatile"
"""
        yaml_path = tmp_path / "gateway_config.yaml"
        yaml_path.write_text(yaml_content)

        # Test loading
        config = GatewayYamlConfig(**yaml.safe_load(yaml_content))
        assert config.openrouter_model_map.opus_5 == "anthropic/claude-opus-5"
        assert config.groq_model_map.llama3 == "llama-3.3-70b-versatile"


class TestModelMapProperties:
    def test_openrouter_model_map_property(self):
        config = GatewayConfig()
        # Access via property triggers YAML load
        models = config.openrouter_model_map
        assert isinstance(models, dict)
        assert "opus-5" in models

    def test_opencode_bridge_model_map_property(self):
        config = GatewayConfig()
        models = config.opencode_bridge_model_map
        assert isinstance(models, dict)
        assert "groq" in models
        assert "gemini" in models