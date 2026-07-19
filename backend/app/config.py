"""
config.py
=========

Why this file exists
---------------------
Every other module in this application (agent nodes, services, database
connection, logging) needs access to configuration values: API keys,
execution limits, database URL, CORS origins, etc.

Rather than scattering `os.getenv(...)` calls across the codebase (which
makes it impossible to know what configuration the app depends on without
grepping every file), we centralize ALL configuration into a single typed
`Settings` object built with `pydantic-settings`.

How it interacts with the rest of the system
---------------------------------------------
- `database/connection.py` reads `settings.database_url` to build the
  SQLAlchemy engine.
- `services/llm_service.py` reads `settings.gemini_api_key` and
  `settings.gemini_model` to construct the Gemini client.
- `agent/graph.py` and `agent/nodes.py` read `settings.max_agent_steps`,
  `settings.max_retries`, and `settings.request_timeout_seconds` to enforce
  loop prevention and timeouts.
- `logging/run_logger.py` reads `settings.log_level` and `settings.log_file`.
- `main.py` reads `settings.cors_origins`, `settings.api_host`, and
  `settings.api_port` to configure the FastAPI app and Uvicorn server.

This module NEVER logs or exposes `gemini_api_key` — consumers must treat
it as a secret and never pass it into logs, tool results, or LLM prompts.
"""

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Strongly-typed application settings loaded from environment variables
    (or a `.env` file in local development).

    Every field has a sane default EXCEPT `gemini_api_key`, which must be
    supplied — the app should fail fast at startup if it is missing rather
    than fail confusingly later when the agent tries to call the LLM.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM Provider ---
    llm_provider: str = Field(
        default="gemini",
        description="Which LLM backend to use: 'gemini', 'github', or 'openrouter'.",
    )

    # Gemini (native)
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key. Required when llm_provider='gemini'.",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model identifier used for agent reasoning and generation.",
    )

    # GitHub Models (OpenAI-compatible endpoint, e.g. Llama, DeepSeek)
    github_token: str = Field(
        default="",
        description="GitHub PAT with 'models: read' permission. Required when llm_provider='github'.",
    )
    github_model: str = Field(
        default="meta/Llama-3.3-70B-Instruct",
        description="GitHub Models model ID, e.g. 'meta/Llama-3.3-70B-Instruct' or 'deepseek/DeepSeek-V3'.",
    )
    github_models_base_url: str = Field(
        default="https://models.github.ai/inference",
        description="GitHub Models OpenAI-compatible inference endpoint.",
    )

    # OpenRouter (OpenAI-compatible endpoint, many models)
    openrouter_api_key: str = Field(
        default="",
        description="OpenRouter API key. Required when llm_provider='openrouter'.",
    )
    openrouter_model: str = Field(
        default="deepseek/deepseek-chat",
        description="OpenRouter model ID, e.g. 'deepseek/deepseek-chat' or 'meta-llama/llama-3.3-70b-instruct'.",
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter's OpenAI-compatible inference endpoint.",
    )

    # --- Database ---
    database_url: str = Field(
        default="sqlite:///./productivity_agent.db",
        description="SQLAlchemy-compatible database URL.",
    )

    # --- Agent Execution Limits (loop prevention / safety) ---
    max_agent_steps: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Maximum number of graph steps allowed per agent run before forced termination.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum number of retries for a failed tool call or LLM call.",
    )
    request_timeout_seconds: int = Field(
        default=30,
        ge=1,
        description="Maximum wall-clock time allowed for a single agent run.",
    )

    # --- Logging ---
    log_level: str = Field(default="INFO", description="Python logging level.")
    log_file: str = Field(
        default="logs/agent_runs.log",
        description="Path to the log file for structured run logs.",
    )

    # --- CORS ---
    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
        description="Comma-separated list of allowed CORS origins.",
    )

    # --- API Server ---
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("llm_provider")
    @classmethod
    def _validate_llm_provider(cls, v: str) -> str:
        """Ensure llm_provider is one of the three supported backends."""
        allowed = {"gemini", "github", "openrouter"}
        lower = v.lower().strip()
        if lower not in allowed:
            raise ValueError(f"llm_provider must be one of {allowed}, got {v!r}")
        return lower

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        """Ensure log_level is one of Python's standard logging levels."""
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper

    @property
    def cors_origins(self) -> List[str]:
        """Parse the comma-separated CORS origins string into a list."""
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    def is_llm_configured(self) -> bool:
        """
        Returns True only if the credential required by the currently
        selected `llm_provider` is present and non-empty. Used by
        `main.py` at startup to fail fast with a clear error message
        instead of letting the agent crash cryptically on the first LLM call.
        """
        if self.llm_provider == "github":
            return bool(self.github_token and self.github_token.strip())
        if self.llm_provider == "openrouter":
            return bool(self.openrouter_api_key and self.openrouter_api_key.strip())
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    @property
    def active_model_name(self) -> str:
        """The human-readable model identifier for whichever provider is active."""
        if self.llm_provider == "github":
            return self.github_model
        if self.llm_provider == "openrouter":
            return self.openrouter_model
        return self.gemini_model


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached singleton `Settings` instance.

    Using `lru_cache` ensures the environment is parsed exactly once per
    process, and every module that calls `get_settings()` receives the same
    object — avoiding redundant environment parsing and guaranteeing
    consistent configuration across the app during a single run.
    """
    return Settings()
