"""
AXIOM Configuration — Pydantic Settings with full environment variable support.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AXIOMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_name: str = "AXIOM"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ── Supabase / PostgreSQL ──────────────────────────────────────────────────
    supabase_url: str = Field(default="http://localhost:54321", description="Supabase project URL")
    supabase_anon_key: str = Field(default="", description="Supabase anonymous key")
    supabase_service_role_key: str = Field(default="", description="Supabase service role key")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/axiom",
        description="Async PostgreSQL connection string",
    )

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    redis_cache_ttl: int = Field(default=3600, description="Cache TTL in seconds")

    # ── AI / Embeddings ────────────────────────────────────────────────────────
    openai_api_key: str = Field(default="", description="OpenAI API key for embeddings")
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model name",
    )
    embedding_dimensions: int = Field(default=1536, description="Embedding vector dimensions")

    # ── Skill Synthesizer (DeepSeek-V3) ───────────────────────────────────────
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="DeepSeek API base URL",
    )
    deepseek_model: str = Field(default="deepseek-chat", description="DeepSeek model identifier")
    synthesis_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    synthesis_max_tokens: int = Field(default=4096)
    synthesis_few_shot_k: int = Field(default=3, description="Number of few-shot examples during synthesis")

    # ── Capability Resolver ────────────────────────────────────────────────────
    resolver_top_k: int = Field(default=10, description="Top-K candidates from vector search")
    resolver_semantic_weight: float = Field(default=0.6)
    resolver_success_weight: float = Field(default=0.3)
    resolver_recency_weight: float = Field(default=0.1)
    resolver_confidence_threshold: float = Field(default=0.45, description="Minimum confidence to return a skill")

    # ── Composition Engine ─────────────────────────────────────────────────────
    composition_max_chain_length: int = Field(default=5, description="Max skills in a composition chain")
    composition_max_paths: int = Field(default=20, description="Max paths explored in graph search")

    # ── Deduplication ─────────────────────────────────────────────────────────
    dedup_cosine_threshold: float = Field(
        default=0.92,
        description="Cosine similarity above which a skill is considered a duplicate",
    )

    # ── Sandbox Limits ────────────────────────────────────────────────────────
    sandbox_memory_limit_mb: int = Field(default=256, description="Max memory per sandbox execution (MB)")
    sandbox_timeout_seconds: int = Field(default=30, description="Max execution time per sandbox run (s)")
    sandbox_allow_network: bool = Field(default=False, description="Allow network access inside sandbox")
    sandbox_max_output_bytes: int = Field(default=1_048_576, description="Max stdout/stderr size (1 MB)")

    # ── Promotion ─────────────────────────────────────────────────────────────
    promotion_min_success_rate: float = Field(
        default=0.8,
        description="Minimum pass rate in sandbox to promote a skill",
    )
    promotion_min_test_cases: int = Field(default=3, description="Minimum number of test cases that must pass")
    auto_promote_synthesized_skills: bool = Field(
        default=False,
        description=(
            "If true, sandbox-passing synthesized skills become ACTIVE immediately. "
            "Default false keeps them READY_FOR_AUTHORIZATION until an explicit authority step."
        ),
    )

    # ── Decay Monitor ─────────────────────────────────────────────────────────
    decay_min_invocations: int = Field(default=50, description="Minimum invocations before decay check")
    decay_success_threshold: float = Field(default=0.70, description="Success rate below which skill is flagged")
    decay_idle_days: int = Field(default=30, description="Days of inactivity before deprecation")
    decay_check_interval_minutes: int = Field(default=60, description="How often decay monitor runs")

    # ── Hermes Integration ────────────────────────────────────────────────────
    hermes_skill_dir: str = Field(default="./skills", description="Path to local Hermes skill directory")
    hermes_auto_ingest: bool = Field(
        default=True,
        description="Automatically ingest new local skills into AXIOM registry",
    )

    @field_validator("resolver_semantic_weight", "resolver_success_weight", "resolver_recency_weight")
    @classmethod
    def weights_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Ranking weights must be non-negative")
        return v


settings = AXIOMSettings()
