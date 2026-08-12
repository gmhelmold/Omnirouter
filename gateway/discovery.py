"""
Model Discovery Module

Auto-generates /v1/models payload from config + live bridge endpoints.
Caches results to ~/.claude/cache/gateway-models.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from gateway.config import GatewayConfig


def build_model_entry(model_id: str, display_name: str) -> dict[str, Any]:
    """Create a model entry for /v1/models response."""
    return {
        "id": model_id,
        "display_name": display_name,
        "owned_by": "claude-gateway",
    }


def build_discovery_payload(config: GatewayConfig) -> dict[str, list[dict[str, Any]]]:
    """
    Build complete discovery payload from config.

    Returns:
        {"data": [{"id": "...", "display_name": "..."}, ...]}
    """
    entries = []

    # Providers whose whole catalog is free-tier. Coerced defensively so a
    # mock/partial config can't blow up discovery.
    ftp = getattr(config, "free_tier_providers", None)
    free_providers = set(ftp) if isinstance(ftp, (list, set, tuple)) else set()

    def is_free(provider: str, key: str, slug: str) -> bool:
        """A model is free if its provider is free-tier, its slug ends in
        ':free' (OpenRouter), or its key is named 'free-*'."""
        return (
            provider in free_providers
            or str(slug).endswith(":free")
            or str(key).lower().startswith("free")
        )

    def label(provider_label: str, free: bool) -> str:
        return f"{provider_label} · FREE 🆓" if free else provider_label

    # provider_id, human label, model map
    simple_providers = [
        ("openrouter", "OpenRouter", config.openrouter_model_map),
        ("groq", "Groq", config.groq_model_map),
        ("gemini", "Gemini", config.gemini_model_map),
        ("nim", "NIM", config.nim_model_map),
        ("mistral", "Mistral", config.mistral_model_map),
        ("cerebras", "Cerebras", config.cerebras_model_map),
    ]
    for provider, provider_label, model_map in simple_providers:
        for key, slug in model_map.items():
            free = is_free(provider, key, slug)
            entries.append(build_model_entry(
                f"claude-{provider}-{key}",
                f"{slug} ({label(provider_label, free)})",
            ))

    # opencode (flat) — a local `opencode serve` instance, opencode's own
    # hosted models. All free.
    for key, slug in config.opencode_model_map.items():
        entries.append(build_model_entry(
            f"claude-opencode-{key}",
            f"{slug} ({label('opencode', True)})",
        ))

    # Validate all IDs contain 'claude' or 'anthropic'
    for entry in entries:
        id_ = entry["id"]
        if "claude" not in id_.lower() and "anthropic" not in id_.lower():
            raise ValueError(
                f"Model ID '{id_}' will be filtered by Claude Code discovery (missing 'claude'/'anthropic')"
            )

    return {"data": entries}


async def fetch_live_bridge_models(config: GatewayConfig) -> list[dict[str, Any]]:
    """
    Fetch live models from the OpenCode Zen endpoint (OpenAI-style ``/models``)
    so the menu reflects whatever Zen currently offers.

    Each Zen modelID is exposed as ``claude-opencode-<modelID>``. Returns entries
    to merge with the static config; silently empty if Zen is unreachable (the
    static map is used instead).
    """
    live_entries: list[dict[str, Any]] = []
    base = config.opencode_base_url.rstrip("/")
    api_key = config.opencode_api_key or "public"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if response.status_code != 200:
                return []
            data = response.json()
    except Exception:
        return []

    models = data.get("data") if isinstance(data, dict) else data
    if not isinstance(models, list):
        return []

    # Skip models the static map already covers (by Zen modelID) so we don't
    # list the same model twice under its friendly key and its raw id.
    known = set(config.opencode_model_map.values())

    for model in models:
        model_id = model.get("id") if isinstance(model, dict) else None
        if not model_id or model_id in known:
            continue
        gw_id = f"claude-opencode-{model_id}"
        if "claude" in gw_id.lower() or "anthropic" in gw_id.lower():
            live_entries.append({
                "id": gw_id,
                "display_name": f"{model_id} (opencode · FREE 🆓)",
            })

    return live_entries


class DiscoveryCache:
    """Handles caching of discovery payload."""

    def __init__(self, cache_path: Path, ttl: int):
        self.cache_path = cache_path
        self.ttl = ttl
        self._cached_payload: dict[str, Any] | None = None
        self._cached_at: float = 0

    def is_stale(self) -> bool:
        return time.time() - self._cached_at > self.ttl

    def load(self) -> dict[str, Any] | None:
        """Load from disk cache if valid."""
        if self._cached_payload is not None and not self.is_stale():
            return self._cached_payload

        if self.cache_path.exists():
            try:
                with self.cache_path.open("r") as f:
                    data = json.load(f)
                cached_at = data.get("_cached_at", 0)
                if time.time() - cached_at <= self.ttl:
                    self._cached_payload = data.get("payload")
                    self._cached_at = cached_at
                    return self._cached_payload
            except Exception:
                pass
        return None

    def save(self, payload: dict[str, Any]) -> None:
        """Save to disk cache."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "payload": payload,
            "_cached_at": time.time(),
        }
        with self.cache_path.open("w") as f:
            json.dump(data, f)
        self._cached_payload = payload
        self._cached_at = time.time()


# Global cache instance
_discovery_cache: DiscoveryCache | None = None


def get_discovery_cache(config: GatewayConfig) -> DiscoveryCache:
    global _discovery_cache
    if _discovery_cache is None:
        _discovery_cache = DiscoveryCache(config.discovery_cache_path, config.discovery_cache_ttl)
    return _discovery_cache


async def get_discovery_payload(config: GatewayConfig) -> dict[str, list[dict[str, Any]]]:
    """
    Get complete discovery payload (static + live bridge models).

    Uses cache if valid, otherwise rebuilds.
    """
    cache = get_discovery_cache(config)

    # Try cache first
    cached = cache.load()
    if cached is not None:
        # Still fetch live bridge models in background to refresh
        # For now, return cached and let next request get fresh data
        return cached

    # Build from static config
    payload = build_discovery_payload(config)

    # Fetch live bridge models
    live_entries = await fetch_live_bridge_models(config)
    if live_entries:
        # Merge: live entries override static for same ID
        existing_ids = {e["id"] for e in payload["data"]}
        for entry in live_entries:
            if entry["id"] not in existing_ids:
                payload["data"].append(entry)

    # Save to cache
    cache.save(payload)

    return payload
