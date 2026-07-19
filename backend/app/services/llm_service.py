"""
services/llm_service.py
=========================

Why this file exists
---------------------
Every LLM call in the system — intent analysis, tool argument extraction,
response generation, and reasoning-heavy tools like `generate_work_plan`
or `extract_meeting_actions` — goes through this single wrapper around
`langchain_google_genai.ChatGoogleGenerativeAI`.

Centralizing this gives us, in one place:
- Retry logic (bounded by `settings.max_retries`) for transient failures.
- A hard timeout (`settings.request_timeout_seconds`) so a hung LLM call
  can never make the whole agent run hang forever.
- Consistent error normalization — every failure mode (missing key,
  network error, malformed response) is translated into an `LLMServiceError`
  subtype that callers can catch and handle uniformly.
- A `generate_json` helper for tools/nodes that need structured output
  (e.g. "extract action items as a JSON list") without each caller
  reimplementing prompt-wrapping and JSON-fence stripping.

How it interacts with the rest of the system
-----------------------------------------------
- Reads `settings.gemini_api_key`, `settings.gemini_model`,
  `settings.max_retries`, `settings.request_timeout_seconds` from `config.py`.
- Used by `agent/nodes.py` (intent analysis, tool selection, response
  generation) and by reasoning tools in `tools/planning_tools.py` and
  `tools/report_tools.py`.
- NEVER logs the API key. NEVER includes it in any exception message.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMServiceError(Exception):
    """Base class for all LLM service errors."""


class LLMConfigurationError(LLMServiceError):
    """Raised when the LLM cannot be initialized due to missing/invalid config."""


class LLMTimeoutError(LLMServiceError):
    """Raised when an LLM call exceeds the configured timeout."""


class LLMResponseError(LLMServiceError):
    """Raised when the LLM returns a response that cannot be parsed/used."""


class LLMService:
    """
    Thin, resilient wrapper around the Gemini chat model.

    Instantiated once (see `get_llm_service` singleton below) and reused
    across all agent nodes and tools within a process.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.is_llm_configured():
            credential_hint = {
                "gemini": "GEMINI_API_KEY",
                "github": "GITHUB_TOKEN",
                "openrouter": "OPENROUTER_API_KEY",
            }.get(self.settings.llm_provider, "the appropriate API key")
            raise LLMConfigurationError(
                f"LLM_PROVIDER is set to {self.settings.llm_provider!r} but "
                f"{credential_hint} is not configured. Cannot initialize LLMService."
            )
        self._model = None  # lazily constructed — see `_get_model()`
        self._override_provider: Optional[str] = None
        self._override_model: Optional[str] = None

    def configure(self, provider: Optional[str] = None, model: Optional[str] = None) -> None:
        """Override the provider/model for subsequent LLM calls in this process."""
        if provider is not None:
            self._override_provider = provider.strip().lower() or None
        if model is not None:
            self._override_model = model.strip() or None
        self._model = None

    def _get_model(self):
        """
        Lazily construct the chat model for whichever provider is
        configured via `settings.llm_provider`:

        - 'gemini': native `langchain_google_genai.ChatGoogleGenerativeAI`.
        - 'github': GitHub Models' OpenAI-compatible endpoint (Llama,
          DeepSeek, and others), accessed via `langchain_openai.ChatOpenAI`
          pointed at `settings.github_models_base_url` with the GitHub PAT
          as the bearer token.
        - 'openrouter': OpenRouter's OpenAI-compatible endpoint, same
          `ChatOpenAI` client pointed at `settings.openrouter_base_url`.

        Lazy construction (rather than doing this in `__init__`) means
        importing this module never triggers a network call or SDK
        handshake — only the first actual `.invoke()` does. This matters
        for unit tests that import the module but mock `.invoke()`.
        """
        if self._model is not None:
            return self._model

        provider = self._override_provider or self.settings.llm_provider

        if provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._model = ChatGoogleGenerativeAI(
                model=self._override_model or self.settings.gemini_model,
                google_api_key=self.settings.gemini_api_key,
                temperature=0.2,
                timeout=self.settings.request_timeout_seconds,
            )
        elif provider == "github":
            from langchain_openai import ChatOpenAI

            self._model = ChatOpenAI(
                model=self._override_model or self.settings.github_model,
                api_key=self.settings.github_token,
                base_url=self.settings.github_models_base_url,
                temperature=0.2,
                timeout=self.settings.request_timeout_seconds,
            )
        elif provider == "openrouter":
            from langchain_openai import ChatOpenAI

            self._model = ChatOpenAI(
                model=self._override_model or self.settings.openrouter_model,
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url,
                temperature=0.2,
                timeout=self.settings.request_timeout_seconds,
                default_headers={
                    # OpenRouter requests these for routing/analytics; both
                    # are optional but recommended by OpenRouter's docs.
                    "HTTP-Referer": "https://localhost",
                    "X-Title": "Productivity Agent",
                },
            )
        else:  # pragma: no cover - config.py's validator already rejects this
            raise LLMConfigurationError(f"Unknown llm_provider: {provider!r}")

        return self._model

    def invoke(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        Send a prompt to Gemini and return the raw text response.

        Retries up to `settings.max_retries` times on transient failures
        (network errors, rate limits) with a short linear backoff. Does
        NOT retry on configuration errors (retrying a bad API key wastes
        time and quota).

        Raises
        ------
        LLMTimeoutError
            If the call does not complete within `request_timeout_seconds`.
        LLMResponseError
            If all retries are exhausted without a successful response.
        """
        model = self._get_model()
        messages = []
        if system_prompt:
            messages.append(("system", system_prompt))
        messages.append(("human", prompt))

        last_error: Optional[Exception] = None
        for attempt in range(1, self.settings.max_retries + 2):  # +1 initial try, +1 for range inclusivity
            start = time.monotonic()
            try:
                response = model.invoke(messages)
                elapsed = time.monotonic() - start
                logger.info("LLM call succeeded on attempt %d (%.2fs)", attempt, elapsed)
                content = getattr(response, "content", None)
                if not content or not isinstance(content, str):
                    raise LLMResponseError("LLM returned an empty or non-text response.")
                return content
            except Exception as exc:  # noqa: BLE001 - intentionally broad; normalized below
                last_error = exc
                elapsed = time.monotonic() - start
                is_timeout = "timeout" in str(exc).lower() or elapsed >= self.settings.request_timeout_seconds
                logger.warning(
                    "LLM call failed on attempt %d/%d (%.2fs): %s",
                    attempt,
                    self.settings.max_retries + 1,
                    elapsed,
                    exc,
                )
                if is_timeout and attempt >= self.settings.max_retries + 1:
                    raise LLMTimeoutError(
                        f"LLM call timed out after {self.settings.request_timeout_seconds}s "
                        f"and {attempt} attempt(s)."
                    ) from exc
                if attempt < self.settings.max_retries + 1:
                    time.sleep(min(1.0 * attempt, 3.0))  # linear backoff, capped

        raise LLMResponseError(
            f"LLM call failed after {self.settings.max_retries + 1} attempts: {last_error}"
        )

    def generate_json(self, prompt: str, system_prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        Invoke the LLM with an instruction to return ONLY valid JSON, then
        parse and return it as a dict.

        Strips markdown code fences (```json ... ```) defensively, since
        LLMs frequently wrap JSON in them despite instructions not to.

        Raises
        ------
        LLMResponseError
            If the response cannot be parsed as valid JSON after stripping
            fences — the caller (typically a tool) is responsible for
            deciding whether to retry, fall back, or surface the error.
        """
        json_instruction = (
            "Respond with ONLY a valid JSON object. Do not include markdown code "
            "fences, explanations, or any text outside the JSON object."
        )
        full_system_prompt = f"{system_prompt}\n\n{json_instruction}" if system_prompt else json_instruction

        raw = self.invoke(prompt, system_prompt=full_system_prompt)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON response: %s | raw=%r", exc, raw[:500])
            raise LLMResponseError(f"LLM did not return valid JSON: {exc}") from exc


_llm_service_singleton: Optional[LLMService] = None


def get_llm_service(provider: Optional[str] = None, model: Optional[str] = None) -> LLMService:
    """
    Returns a process-wide singleton `LLMService` instance, constructing it
    on first use. Raises `LLMConfigurationError` immediately if the API key
    is missing, so callers get a clear error rather than a cryptic failure
    deep inside a tool call.
    """
    global _llm_service_singleton
    if _llm_service_singleton is None:
        _llm_service_singleton = LLMService()
    if provider is not None or model is not None:
        _llm_service_singleton.configure(provider=provider, model=model)
    return _llm_service_singleton
