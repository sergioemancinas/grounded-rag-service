from __future__ import annotations

from pathlib import Path

from pydantic import Field, ImportString, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; every field maps to one upper-cased env var.

    An empty environment (and an empty or missing .env) boots the full
    offline stack: local hash embeddings, extractive generation, JSONL
    retrieval, no network calls.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"

    # Slack adapter (optional; consumed by the channel adapter, not the core)
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    allowed_channel_ids: str = ""

    # Providers by registry name
    embedding_provider: str = "local"
    generation_provider: str = "local"
    grounding_judge: str = ""  # empty = auto-select from generation_provider
    openai_api_key: str = ""
    openai_generation_model: str = "gpt-4o"
    openai_expansion_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # Providers by dotted path (escape hatches; win over registry names).
    # These import and execute operator-supplied code at startup: set them
    # only from the environment or .env, never from request data.
    embedder_class: ImportString | None = None
    generator_class: ImportString | None = None
    reranker_class: ImportString | None = None
    retriever_class: ImportString | None = None
    grounding_judge_class: ImportString | None = None

    index_path: Path = Path("data/index.jsonl")
    feedback_db_path: Path = Path("data/feedback.sqlite3")
    # Secret keying the feedback digests. Unset derives a random per-process
    # key: digests stay unlinkable but cannot be compared across restarts.
    feedback_hmac_key: str = ""

    # Prompt overrides: directory of *.md files shadowing app/prompts/
    prompts_dir: Path | None = None

    # Optional static bearer token protecting /v1/* (empty = no auth)
    api_auth_token: str = ""

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

    mcp_port: int = 8090
    mcp_auth_mode: str = "off"  # "off" (local demo only) | "jwt"
    mcp_auth_issuer: str = ""
    mcp_auth_audience: str = ""
    mcp_auth_jwks_url: str = ""  # optional; discovered from the issuer when empty
    mcp_auth_algorithms: str = "RS256"  # accepted JWT algorithms, comma-separated
    mcp_resource_url: str = ""  # canonical public URL of this MCP server, including /mcp
    mcp_extensions_module: str = ""  # module exposing register(server, deps, settings)
    mcp_required_scopes: str = ""  # comma-separated scopes required on MCP tokens
    mcp_tool_search_description: str = ""
    mcp_tool_fetch_description: str = ""
    mcp_tool_ask_description: str = ""

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local", "test"}

    @property
    def allowed_channels(self) -> set[str]:
        return {part.strip() for part in self.allowed_channel_ids.split(",") if part.strip()}

    @property
    def mcp_scopes(self) -> set[str]:
        return {part.strip() for part in self.mcp_required_scopes.split(",") if part.strip()}

    @model_validator(mode="after")
    def _validate_provider_credentials(self) -> Settings:
        """Fail fast at startup on inconsistent provider configuration."""
        if self.embedding_provider == "openai" and self.embedder_class is None and not self.openai_api_key:
            raise ValueError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        if self.generation_provider == "openai" and self.generator_class is None and not self.openai_api_key:
            raise ValueError("GENERATION_PROVIDER=openai requires OPENAI_API_KEY")
        if self.grounding_judge == "llm" and self.grounding_judge_class is None and not self.openai_api_key:
            raise ValueError("GROUNDING_JUDGE=llm requires OPENAI_API_KEY")
        return self
