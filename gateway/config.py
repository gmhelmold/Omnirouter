"""
Claude Code Gateway - Configuration Module

State-of-the-art configuration using Pydantic Settings v2 with:
- Environment variable loading with validation
- YAML config file loading with schema validation
- Type-safe nested configurations
- Computed fields for derived values
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenRouterModelMap(BaseModel):
    opus_5: str = Field(default="anthropic/claude-opus-5", alias="opus-5")
    sonnet_5: str = Field(default="anthropic/claude-sonnet-5", alias="sonnet-5")
    haiku_5: str = Field(default="anthropic/claude-3.5-haiku", alias="haiku-5")
    deepseek: str = Field(default="deepseek/deepseek-chat")
    qwen_coder: str = Field(default="qwen/qwen-2.5-coder-32b-instruct", alias="qwen-coder")
    glm_5: str = Field(default="z-ai/glm-5.2", alias="glm-5")

    model_config = {"populate_by_name": True, "extra": "allow"}


class GroqModelMap(BaseModel):
    llama3: str = Field(default="llama-3.3-70b-versatile")
    mixtral: str = Field(default="mixtral-8x7b-32768")
    gemma: str = Field(default="gemma2-9b-it")

    model_config = {"extra": "allow"}


class GeminiModelMap(BaseModel):
    flash: str = Field(default="gemini-1.5-flash")
    pro: str = Field(default="gemini-1.5-pro")
    flash_8b: str = Field(default="gemini-1.5-flash-8b", alias="flash-8b")

    model_config = {"populate_by_name": True, "extra": "allow"}


class NimModelMap(BaseModel):
    llama3: str = Field(default="meta/llama-3.1-70b-instruct")
    nemotron: str = Field(default="nvidia/nemotron-3-ultra")
    mixtral: str = Field(default="mistralai/mixtral-8x7b-instruct-v0.1")

    model_config = {"extra": "allow"}


class MistralModelMap(BaseModel):
    large: str = Field(default="mistral-large-latest")
    small: str = Field(default="mistral-small-latest")
    codestral: str = Field(default="codestral-latest")

    model_config = {"extra": "allow"}


class OpencodeBridgeModelMap(BaseModel):
    groq: GroqModelMap = Field(default_factory=GroqModelMap)
    gemini: GeminiModelMap = Field(default_factory=GeminiModelMap)
    mistral: MistralModelMap = Field(default_factory=MistralModelMap)

    model_config = {"extra": "allow"}


class FallbackChains(BaseModel):
    groq: list[str] = Field(default_factory=lambda: ["groq", "openrouter-groq", "opencode-groq"])
    gemini: list[str] = Field(default_factory=lambda: ["gemini", "opencode-gemini", "openrouter-gemini"])
    mistral: list[str] = Field(default_factory=lambda: ["mistral", "opencode-mistral", "openrouter-mistral"])
    nim: list[str] = Field(default_factory=lambda: ["nim", "openrouter-nim"])
    openrouter: list[str] = Field(default_factory=lambda: ["openrouter"])
    opencode: list[str] = Field(default_factory=lambda: ["opencode"])

    model_config = {"extra": "allow"}


class GatewayYamlConfig(BaseModel):
    """Full YAML configuration schema."""

    openrouter_model_map: OpenRouterModelMap = Field(default_factory=OpenRouterModelMap)
    groq_model_map: GroqModelMap = Field(default_factory=GroqModelMap)
    gemini_model_map: GeminiModelMap = Field(default_factory=GeminiModelMap)
    nim_model_map: NimModelMap = Field(default_factory=NimModelMap)
    mistral_model_map: MistralModelMap = Field(default_factory=MistralModelMap)
    opencode_bridge_model_map: OpencodeBridgeModelMap = Field(default_factory=OpencodeBridgeModelMap)
    opencode_bridge_endpoints: dict[str, str] = Field(default_factory=lambda: {
        "groq": "http://localhost:5001",
        "gemini": "http://localhost:5002",
        "mistral": "http://localhost:5003",
    })
    fallback_chains: FallbackChains = Field(default_factory=FallbackChains)

    model_config = {"extra": "allow"}


class GatewayConfig(BaseSettings):
    """Main gateway configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # Server
    port: int = Field(default=8787, alias="GATEWAY_PORT")
    host: str = Field(default="127.0.0.1", alias="GATEWAY_HOST")
    log_level: str = Field(default="INFO", alias="GATEWAY_LOG_LEVEL")

    # Provider API Keys (conditional - only required if using that provider)
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api", alias="OPENROUTER_BASE_URL")

    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")

    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")

    nim_base_url: str | None = Field(default=None, alias="NIM_BASE_URL")
    nim_api_key: str | None = Field(default=None, alias="NIM_API_KEY")

    mistral_api_key: str | None = Field(default=None, alias="MISTRAL_API_KEY")

    # Discovery
    discovery_cache_ttl: int = Field(default=300, alias="DISCOVERY_CACHE_TTL")  # seconds
    discovery_cache_path: Path = Field(
        default=Path.home() / ".claude" / "cache" / "gateway-models.json",
        alias="DISCOVERY_CACHE_PATH",
    )

    # Timeouts
    request_timeout: float = Field(default=600.0, alias="REQUEST_TIMEOUT")  # 10 min
    connect_timeout: float = Field(default=10.0, alias="CONNECT_TIMEOUT")
    keepalive_interval: float = Field(default=30.0, alias="KEEPALIVE_INTERVAL")

    # Rate Limiting (local enforcement for free tiers)
    gemini_rpm: int = Field(default=15, alias="GEMINI_RPM")
    mistral_rpm: int = Field(default=500, alias="MISTRAL_RPM")

    # Fallback
    max_fallback_attempts: int = Field(default=3, alias="MAX_FALLBACK_ATTEMPTS")

    # YAML config (loaded separately)
    _yaml_config: GatewayYamlConfig | None = None
    _yaml_path: ClassVar[Path] = Path("gateway_config.yaml")

    def load_yaml(self) -> GatewayYamlConfig:
        """Load and cache YAML configuration."""
        if self._yaml_config is None:
            if self._yaml_path.exists():
                with self._yaml_path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self._yaml_config = GatewayYamlConfig(**data)
            else:
                self._yaml_config = GatewayYamlConfig()
        return self._yaml_config

    @property
    def yaml(self) -> GatewayYamlConfig:
        return self.load_yaml()

    # Computed properties for easy access
    @property
    def openrouter_model_map(self) -> dict[str, str]:
        return self.yaml.openrouter_model_map.model_dump(by_alias=True)

    @property
    def groq_model_map(self) -> dict[str, str]:
        return self.yaml.groq_model_map.model_dump()

    @property
    def gemini_model_map(self) -> dict[str, str]:
        return self.yaml.gemini_model_map.model_dump(by_alias=True)

    @property
    def nim_model_map(self) -> dict[str, str]:
        return self.yaml.nim_model_map.model_dump()

    @property
    def mistral_model_map(self) -> dict[str, str]:
        return self.yaml.mistral_model_map.model_dump()

    @property
    def opencode_bridge_model_map(self) -> dict[str, dict[str, str]]:
        return {
            "groq": self.yaml.opencode_bridge_model_map.groq.model_dump(),
            "gemini": self.yaml.opencode_bridge_model_map.gemini.model_dump(by_alias=True),
            "mistral": self.yaml.opencode_bridge_model_map.mistral.model_dump(),
        }

    @property
    def opencode_bridge_endpoints(self) -> dict[str, str]:
        return self.yaml.opencode_bridge_endpoints

    @property
    def fallback_chains(self) -> dict[str, list[str]]:
        return self.yaml.fallback_chains.model_dump()


# Global config instance
config = GatewayConfig()


def get_config() -> GatewayConfig:
    """Dependency injection helper for FastAPI."""
    return config