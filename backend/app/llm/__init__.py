from __future__ import annotations

from ..config import Settings
from .base import LLM, Blob, LLMError, Part, PermanentLLMError, TransientLLMError

__all__ = ["LLM", "Blob", "LLMError", "Part", "PermanentLLMError", "TransientLLMError", "make_llm"]


def make_llm(settings: Settings) -> LLM:
    if settings.llm_provider == "gemini":
        from .gemini import GeminiLLM

        return GeminiLLM(settings.gemini_api_key, settings.gemini_model, settings.llm_timeout_s, settings.gemini_thinking or None, settings.gemini_rpm)
    if settings.llm_provider == "openrouter":
        from .openrouter import OpenRouterLLM

        return OpenRouterLLM(
            settings.openrouter_api_key, settings.openrouter_model, settings.llm_timeout_s,
            settings.openrouter_reasoning or None, settings.openrouter_rpm,
        )
    raise PermanentLLMError(f"Unknown LLM_PROVIDER {settings.llm_provider!r}")
