from __future__ import annotations

import warnings
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    embedding_provider: str = "local"
    generation_provider: str = "local"
    openai_api_key: str = ""
    openai_generation_model: str = "gpt-4o"
    openai_expansion_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    index_path: Path = Path("data/index.jsonl")
    feedback_db_path: Path = Path("data/feedback.sqlite3")

    max_context_chunks: int = 10
    max_context_chars_per_chunk: int = 2600
    rerank_enabled: bool = False
    rerank_pool: int = 75
    lexical_scorer: str = "bm25"
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    context_max_per_doc: int = 2
    mmr_lambda: float = Field(default=0.7, ge=0.0, le=1.0)

    grounding_check_enabled: bool = True
    grounding_min_score: float = Field(default=0.55, ge=0.0, le=1.0)

    cache_enabled: bool = True
    cache_similarity: float = Field(default=0.97, ge=0.0, le=1.0)
    cache_ttl_seconds: int = 7200

    router_enabled: bool = True
    allowed_channel_ids: str = ""

    mcp_port: int = 8090
    mcp_auth_mode: str = "off"  # "off" (local demo only) | "jwt"
    mcp_auth_issuer: str = ""
    mcp_auth_audience: str = ""
    mcp_auth_jwks_url: str = ""  # optional; discovered from the issuer when empty
    mcp_resource_url: str = ""  # public URL of this MCP server

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local", "test"}

    @property
    def allowed_channels(self) -> set[str]:
        return {part.strip() for part in self.allowed_channel_ids.split(",") if part.strip()}

    def validate_credentials(self) -> None:
        missing: list[str] = []
        if not self.slack_bot_token:
            missing.append("SLACK_BOT_TOKEN")
        if not self.slack_signing_secret:
            missing.append("SLACK_SIGNING_SECRET")
        if missing:
            message = f"Missing required Slack credentials: {', '.join(missing)}"
            if self.is_dev:
                warnings.warn(message, RuntimeWarning, stacklevel=2)
                return
            raise RuntimeError(message)
