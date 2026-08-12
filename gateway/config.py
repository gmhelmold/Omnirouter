"""
Claude Code Gateway - Configuration Module

State-of-the-art configuration using Pydantic Settings v2 with:
- Environment variable loading with validation
- YAML config file loading with schema validation
- Type-safe nested configurations
- Computed fields for derived values
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenRouterModelMap(BaseModel):
    # Premium / paid (valid current OpenRouter slugs)
    opus_5: str = Field(default="anthropic/claude-opus-5", alias="opus-5")
    opus_48: str = Field(default="anthropic/claude-opus-4.8", alias="opus-4.8")
    sonnet_5: str = Field(default="anthropic/claude-sonnet-5", alias="sonnet-5")
    haiku_45: str = Field(default="anthropic/claude-haiku-4.5", alias="haiku-4.5")
    deepseek: str = Field(default="deepseek/deepseek-chat-v3.1")
    qwen_coder: str = Field(default="qwen/qwen3-coder", alias="qwen-coder")
    # Free tier — any slug ending in ":free" is auto-tagged FREE in discovery.
    free_nemotron_ultra: str = Field(
        default="nvidia/nemotron-3-ultra-550b-a55b:free", alias="free-nemotron-ultra"
    )
    free_nemotron_super: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b:free", alias="free-nemotron-super"
    )
    free_gemma: str = Field(default="google/gemma-4-31b-it:free", alias="free-gemma")
    free_gpt_oss: str = Field(default="openai/gpt-oss-20b:free", alias="free-gpt-oss")
    free_north_code: str = Field(default="cohere/north-mini-code:free", alias="free-north-code")

    model_config = {"populate_by_name": True, "extra": "allow"}


class GroqModelMap(BaseModel):
    # mixtral-8x7b-32768 and gemma2-9b-it were retired by Groq. Extra keys
    # (gpt-oss, qwen3, compound, ...) are supplied via YAML (extra="allow").
    llama3: str = Field(default="llama-3.3-70b-versatile")

    model_config = {"extra": "allow"}


class GeminiModelMap(BaseModel):
    # gemini-1.5-* were retired. flash/pro point at the rolling "-latest"
    # aliases; specific versions (2.5/3.x, gemma-4) are added via YAML.
    flash: str = Field(default="gemini-flash-latest")
    pro: str = Field(default="gemini-pro-latest")

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


class CerebrasModelMap(BaseModel):
    gpt_oss: str = Field(default="gpt-oss-120b", alias="gpt-oss")
    glm: str = Field(default="zai-glm-4.7")

    model_config = {"populate_by_name": True, "extra": "allow"}


class OpencodeModelMap(BaseModel):
    """Flat map of gateway key -> opencode provider modelID, served by a local
    ``opencode serve`` instance (opencode's own hosted "zen" models). All free.
    Keys are exposed as `claude-opencode-<key>`. Hyphenated keys come in via
    YAML (extra="allow"); a few valid-identifier ones are declared here so a
    bare config still has sensible defaults."""

    big_pickle: str = Field(default="big-pickle", alias="big-pickle")
    deepseek_v4_flash: str = Field(default="deepseek-v4-flash-free", alias="deepseek-v4-flash")
    hy3: str = Field(default="hy3-free")
    mimo: str = Field(default="mimo-v2.5-free")

    model_config = {"populate_by_name": True, "extra": "allow"}


class FallbackChains(BaseModel):
    # Entries are registered backend provider_names. The primary is implicit
    # (it is the backend that owns the model prefix); these are the *fallbacks*
    # tried after it, in order. opencode is NOT a fallback for other providers:
    # its `opencode serve` instance only exposes opencode's own hosted models,
    # not groq/gemini/mistral catalogs, so a shared model key would not map.
    groq: list[str] = Field(default_factory=list)
    gemini: list[str] = Field(default_factory=list)
    mistral: list[str] = Field(default_factory=list)
    nim: list[str] = Field(default_factory=list)
    cerebras: list[str] = Field(default_factory=list)
    openrouter: list[str] = Field(default_factory=list)
    opencode: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class GatewayYamlConfig(BaseModel):
    """Full YAML configuration schema."""

    openrouter_model_map: OpenRouterModelMap = Field(default_factory=OpenRouterModelMap)
    groq_model_map: GroqModelMap = Field(default_factory=GroqModelMap)
    gemini_model_map: GeminiModelMap = Field(default_factory=GeminiModelMap)
    nim_model_map: NimModelMap = Field(default_factory=NimModelMap)
    mistral_model_map: MistralModelMap = Field(default_factory=MistralModelMap)
    cerebras_model_map: CerebrasModelMap = Field(default_factory=CerebrasModelMap)
    # opencode is a standalone provider backed by a local `opencode serve`
    # instance (session API), exposing opencode's own hosted models.
    opencode_model_map: OpencodeModelMap = Field(default_factory=OpencodeModelMap)
    opencode_serve_url: str = Field(default="http://127.0.0.1:5051")
    opencode_agent: str = Field(default="general")
    fallback_chains: FallbackChains = Field(default_factory=FallbackChains)
    # Providers whose entire catalog is free-tier. Every model from these is
    # badged FREE in discovery. (OpenRouter is mixed, so its free models are
    # detected per-slug instead — any ":free" slug or "free-" key.)
    free_tier_providers: list[str] = Field(
        default_factory=lambda: ["groq", "gemini", "mistral", "cerebras"]
    )

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

    cerebras_api_key: str | None = Field(default=None, alias="CEREBRAS_API_KEY")

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
    cerebras_rpm: int = Field(default=5, alias="CEREBRAS_RPM")

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
    def cerebras_model_map(self) -> dict[str, str]:
        return self.yaml.cerebras_model_map.model_dump(by_alias=True)

    @property
    def opencode_model_map(self) -> dict[str, str]:
        return self.yaml.opencode_model_map.model_dump(by_alias=True)

    @property
    def opencode_serve_url(self) -> str:
        return self.yaml.opencode_serve_url

    @property
    def opencode_agent(self) -> str:
        return self.yaml.opencode_agent

    @property
    def fallback_chains(self) -> dict[str, list[str]]:
        return self.yaml.fallback_chains.model_dump()

    @property
    def free_tier_providers(self) -> list[str]:
        return self.yaml.free_tier_providers


# Global config instance
config = GatewayConfig()


def get_config() -> GatewayConfig:
    """Dependency injection helper for FastAPI."""
    return config
