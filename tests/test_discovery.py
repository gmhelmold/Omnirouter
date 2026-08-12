"""
Tests for discovery module.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.discovery import (
    build_discovery_payload,
    build_model_entry,
    DiscoveryCache,
    fetch_live_bridge_models,
)


class TestBuildDiscoveryPayload:
    def test_basic_structure(self):
        config = MagicMock()
        config.openrouter_model_map = {"opus-5": "Opus 5"}
        config.groq_model_map = {"llama3": "Llama 3"}
        config.gemini_model_map = {"flash": "Gemini Flash"}
        config.nim_model_map = {"llama3": "NIM Llama"}
        config.mistral_model_map = {"large": "Mistral Large"}
        config.opencode_bridge_model_map = {
            "groq": {"llama3": "Groq via opencode"},
            "gemini": {"flash": "Gemini via opencode"},
        }

        payload = build_discovery_payload(config)

        assert "data" in payload
        assert isinstance(payload["data"], list)
        assert len(payload["data"]) == 6

    def test_ids_contain_claude(self):
        config = MagicMock()
        config.openrouter_model_map = {"opus-5": "Opus 5"}
        config.groq_model_map = {}
        config.gemini_model_map = {}
        config.nim_model_map = {}
        config.mistral_model_map = {}
        config.opencode_bridge_model_map = {}

        payload = build_discovery_payload(config)

        for entry in payload["data"]:
            id_ = entry["id"]
            assert "claude" in id_.lower() or "anthropic" in id_.lower()

    def test_display_names(self):
        config = MagicMock()
        config.openrouter_model_map = {"opus-5": "Opus 5"}
        config.groq_model_map = {}
        config.gemini_model_map = {}
        config.nim_model_map = {}
        config.mistral_model_map = {}
        config.opencode_bridge_model_map = {}

        payload = build_discovery_payload(config)
        entry = payload["data"][0]
        assert entry["display_name"] == "Opus 5 (OpenRouter)"


class TestBuildModelEntry:
    def test_basic_entry(self):
        entry = build_model_entry("claude-test-model", "Test Model")
        assert entry["id"] == "claude-test-model"
        assert entry["display_name"] == "Test Model"
        assert entry["owned_by"] == "claude-gateway"


class TestDiscoveryCache:
    def test_cache_save_load(self, tmp_path):
        cache_path = tmp_path / "test_cache.json"
        cache = DiscoveryCache(cache_path, ttl=300)

        payload = {"data": [{"id": "test", "display_name": "Test"}]}
        cache.save(payload)

        loaded = cache.load()
        assert loaded == payload

    def test_cache_expiry(self, tmp_path):
        cache_path = tmp_path / "test_cache.json"
        cache = DiscoveryCache(cache_path, ttl=1)  # 1 second TTL

        payload = {"data": [{"id": "test"}]}
        cache.save(payload)

        # Immediately available
        assert cache.load() == payload

        # Wait for expiry
        import time
        time.sleep(1.1)
        assert cache.load() is None

    def test_cache_missing_file(self, tmp_path):
        cache_path = tmp_path / "nonexistent.json"
        cache = DiscoveryCache(cache_path, ttl=300)
        assert cache.load() is None


class TestFetchLiveBridgeModels:
    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        config = MagicMock()
        config.opencode_bridge_endpoints = {
            "groq": "http://localhost:5001",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "llama-3.3-70b", "display_name": "Llama 3.3 70B"}
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            entries = await fetch_live_bridge_models(config)

            assert len(entries) == 1
            assert entries[0]["id"] == "claude-opencode-groq-llama-3.3-70b"

    @pytest.mark.asyncio
    async def test_failed_fetch(self):
        config = MagicMock()
        config.opencode_bridge_endpoints = {"groq": "http://localhost:5001"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get.side_effect = Exception("Connection refused")
            mock_client_class.return_value = mock_client

            entries = await fetch_live_bridge_models(config)
            assert entries == []