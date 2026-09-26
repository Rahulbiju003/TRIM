"""LiteLLM-based completion backend.

Wraps litellm.completion so the rest of the codebase never touches litellm
directly. All providers (OpenRouter, Gemini, OpenAI, Anthropic, Ollama …)
are selected by the WORKER_MODEL env-var string — no code changes needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import litellm
import litellm.exceptions

from worker import config
from worker import routing as _routing


@dataclass
class CompletionResult:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LiteLLMBackend:
    """Single backend that delegates to any litellm-supported provider."""

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        self.model = model or config.WORKER_MODEL
        # Explicit arg wins; fall back to config; None = let LiteLLM/provider decide
        self.temperature: float | None = (
            temperature if temperature is not None else config.WORKER_TEMPERATURE
        )
        # Suppress verbose litellm logs unless caller opts in
        litellm.suppress_debug_info = True
        os.environ.setdefault("LITELLM_LOG", "ERROR")

        # Temperature guard: check at init for the primary model
        try:
            info = litellm.get_model_info(self.model)
            self._skip_temperature: bool = bool(info.get("supports_reasoning", False))
        except Exception:
            self._skip_temperature = True  # safe default: unknown models skip temperature

    def complete(
        self, system: str, user: str, *, model: str | None = None
    ) -> CompletionResult:
        """Synchronous completion. Raises on error (caller handles fail-open).

        model: optional per-call override. When provided, that model is used
               directly with no context-window fallback (the caller has already
               chosen the right model for the content type).
        """
        if model and model != self.model:
            # Explicit override — bypass fallback logic
            return self._do_complete(model, system, user)
        try:
            return self._do_complete(self.model, system, user)
        except litellm.exceptions.ContextWindowExceededError:
            fb = _routing.fallback_model()
            if fb:
                return self._do_complete(fb, system, user)
            raise

    def _do_complete(self, model: str, system: str, user: str) -> CompletionResult:
        """Internal completion parameterized by model (supports fallback model)."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "timeout": config.SHUNT_TIMEOUT_SECONDS,
        }
        # Temperature guard applied per-model (fallback may have different rules)
        if self.temperature is not None and not _routing.skip_temperature(model):
            kwargs["temperature"] = self.temperature
        response = litellm.completion(**kwargs)
        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        content: str = (getattr(getattr(choice, "message", None), "content", None) or "") if choice else ""
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        return CompletionResult(
            content=content.strip(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )

    def complete_multimodal(
        self,
        system: str,
        user_parts: list,
        model: str | None = None,
    ) -> CompletionResult:
        """Completion with multimodal message parts (Tier 1 native multimodal).

        user_parts: list of LiteLLM message part dicts (text + image_url).
        model: override model (e.g. vision or PDF model); defaults to self.model.
        """
        use_model = model or self.model
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_parts},
        ]
        kwargs: dict = {
            "model": use_model,
            "messages": messages,
            "timeout": config.SHUNT_TIMEOUT_SECONDS,
        }
        if self.temperature is not None and not _routing.skip_temperature(use_model):
            kwargs["temperature"] = self.temperature
        response = litellm.completion(**kwargs)
        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        content: str = (getattr(getattr(choice, "message", None), "content", None) or "") if choice else ""
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        return CompletionResult(
            content=content.strip(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=use_model,
        )
