"""LiteLLM-based completion backend.

Wraps litellm.completion so the rest of the codebase never touches litellm
directly. All providers (OpenRouter, Gemini, OpenAI, Anthropic, Ollama …)
are selected by the WORKER_MODEL env-var string — no code changes needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import litellm

from worker import config


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
        self.temperature = temperature if temperature is not None else config.WORKER_TEMPERATURE
        # Suppress verbose litellm logs unless caller opts in
        litellm.suppress_debug_info = True
        os.environ.setdefault("LITELLM_LOG", "ERROR")

    def complete(self, system: str, user: str) -> CompletionResult:
        """Synchronous completion. Raises on error (caller handles fail-open)."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response = litellm.completion(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            timeout=config.SHUNT_TIMEOUT_SECONDS,
        )
        choice = response.choices[0]
        content: str = choice.message.content or ""
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        return CompletionResult(
            content=content.strip(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self.model,
        )
