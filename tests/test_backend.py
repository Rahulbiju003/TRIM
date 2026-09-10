"""Tests for worker/backends/litellm_backend.py."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from worker.backends.litellm_backend import CompletionResult, LiteLLMBackend


def _make_litellm_response(content: str | None = "hello", input_tok=100, output_tok=50, model="gpt-4.1-nano"):
    """Build a minimal fake litellm response object."""
    usage = SimpleNamespace(prompt_tokens=input_tok, completion_tokens=output_tok)
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


class TestCompletionResult:
    """CompletionResult dataclass."""

    def test_defaults(self):
        r = CompletionResult(content="hi")
        assert r.content == "hi"
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.model == ""

    def test_all_fields(self):
        r = CompletionResult(content="x", input_tokens=10, output_tokens=5, model="gpt-4.1-nano")
        assert r.input_tokens == 10
        assert r.output_tokens == 5
        assert r.model == "gpt-4.1-nano"


class TestLiteLLMBackendInit:
    """Backend initialisation and configuration."""

    def test_uses_config_model_by_default(self, monkeypatch):
        monkeypatch.setattr("worker.config.WORKER_MODEL", "test-model")
        backend = LiteLLMBackend()
        assert backend.model == "test-model"

    def test_explicit_model_overrides_config(self, monkeypatch):
        monkeypatch.setattr("worker.config.WORKER_MODEL", "config-model")
        backend = LiteLLMBackend(model="explicit-model")
        assert backend.model == "explicit-model"

    def test_temperature_from_config(self, monkeypatch):
        monkeypatch.setattr("worker.config.WORKER_TEMPERATURE", 0.7)
        backend = LiteLLMBackend()
        assert backend.temperature == pytest.approx(0.7)

    def test_explicit_temperature_overrides_config(self, monkeypatch):
        monkeypatch.setattr("worker.config.WORKER_TEMPERATURE", 0.2)
        backend = LiteLLMBackend(temperature=0.9)
        assert backend.temperature == pytest.approx(0.9)

    def test_temperature_zero_is_valid(self):
        backend = LiteLLMBackend(temperature=0.0)
        assert backend.temperature == 0.0


class TestComplete:
    """Backend.complete() — normal and error paths."""

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_returns_completion_result(self, mock_litellm):
        mock_litellm.return_value = _make_litellm_response(
            content="  summary text  ", input_tok=200, output_tok=40
        )
        backend = LiteLLMBackend(model="gpt-4.1-nano")
        result = backend.complete("system prompt", "user message")
        assert isinstance(result, CompletionResult)

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_content_is_stripped(self, mock_litellm):
        mock_litellm.return_value = _make_litellm_response(content="  hello world  ")
        backend = LiteLLMBackend(model="m")
        result = backend.complete("sys", "usr")
        assert result.content == "hello world"

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_token_counts_extracted(self, mock_litellm):
        mock_litellm.return_value = _make_litellm_response(input_tok=1234, output_tok=567)
        backend = LiteLLMBackend(model="m")
        result = backend.complete("sys", "usr")
        assert result.input_tokens == 1234
        assert result.output_tokens == 567

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_model_propagated(self, mock_litellm):
        mock_litellm.return_value = _make_litellm_response()
        backend = LiteLLMBackend(model="special-model")
        result = backend.complete("sys", "usr")
        assert result.model == "special-model"

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_passes_system_and_user_messages(self, mock_litellm):
        mock_litellm.return_value = _make_litellm_response()
        backend = LiteLLMBackend(model="m")
        backend.complete("SYS", "USR")
        call_kwargs = mock_litellm.call_args
        messages = call_kwargs[1]["messages"]
        assert messages[0] == {"role": "system", "content": "SYS"}
        assert messages[1] == {"role": "user", "content": "USR"}

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_passes_timeout(self, mock_litellm, monkeypatch):
        mock_litellm.return_value = _make_litellm_response()
        monkeypatch.setattr("worker.config.SHUNT_TIMEOUT_SECONDS", 99)
        backend = LiteLLMBackend(model="m")
        backend.complete("sys", "usr")
        assert mock_litellm.call_args[1]["timeout"] == 99

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_raises_on_litellm_error(self, mock_litellm):
        mock_litellm.side_effect = RuntimeError("provider down")
        backend = LiteLLMBackend(model="m")
        with pytest.raises(RuntimeError, match="provider down"):
            backend.complete("sys", "usr")

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_none_content_becomes_empty_string(self, mock_litellm):
        response = _make_litellm_response(content=None)
        mock_litellm.return_value = response
        backend = LiteLLMBackend(model="m")
        result = backend.complete("sys", "usr")
        assert result.content == ""

    @patch("worker.backends.litellm_backend.litellm.completion")
    def test_missing_usage_defaults_to_zero(self, mock_litellm):
        response = _make_litellm_response()
        del response.usage  # simulate missing usage attribute
        mock_litellm.return_value = response
        backend = LiteLLMBackend(model="m")
        result = backend.complete("sys", "usr")
        assert result.input_tokens == 0
        assert result.output_tokens == 0
