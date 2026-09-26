"""Tests for worker/routing.py — dynamic model routing."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import worker.routing as routing


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_model_info(**kwargs) -> dict:
    """Build a minimal fake litellm model-info dict."""
    defaults = {
        "max_input_tokens": None,
        "supports_pdf_input": False,
        "supports_vision": False,
        "supports_reasoning": False,
    }
    defaults.update(kwargs)
    return defaults


# ── _model_info ───────────────────────────────────────────────────────────────

class TestModelInfo:
    """routing._model_info() — graceful error handling."""

    def test_returns_dict_on_success(self):
        fake_info = {"max_input_tokens": 100_000, "supports_vision": True}
        with patch("worker.routing.litellm.get_model_info", return_value=fake_info):
            result = routing._model_info("some-model")
        assert result == fake_info

    def test_returns_empty_dict_on_exception(self):
        with patch("worker.routing.litellm.get_model_info", side_effect=Exception("unknown model")):
            result = routing._model_info("unknown-model")
        assert result == {}

    def test_returns_empty_dict_on_key_error(self):
        with patch("worker.routing.litellm.get_model_info", side_effect=KeyError("missing")):
            result = routing._model_info("another-model")
        assert result == {}


# ── compute_max_bytes ─────────────────────────────────────────────────────────

class TestComputeMaxBytes:
    """compute_max_bytes() — dynamic payload ceiling."""

    def test_shunt_max_bytes_override_wins(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", 12345)
        # Should return the override without calling litellm
        with patch("worker.routing.litellm.get_model_info") as mock_info:
            result = routing.compute_max_bytes("any-model")
        assert result == 12345
        mock_info.assert_not_called()

    def test_fallback_when_no_context_info(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        with patch("worker.routing.litellm.get_model_info", return_value={}):
            result = routing.compute_max_bytes("unknown-model")
        assert result == routing._FALLBACK_MAX_BYTES

    def test_fallback_when_max_input_tokens_is_none(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        with patch("worker.routing.litellm.get_model_info", return_value={"max_input_tokens": None}):
            result = routing.compute_max_bytes("model-x")
        assert result == routing._FALLBACK_MAX_BYTES

    def test_fallback_when_max_input_tokens_is_zero(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        with patch("worker.routing.litellm.get_model_info", return_value={"max_input_tokens": 0}):
            result = routing.compute_max_bytes("model-x")
        assert result == routing._FALLBACK_MAX_BYTES

    def test_computes_from_context_window(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        ctx = 100_000
        with patch("worker.routing.litellm.get_model_info", return_value={"max_input_tokens": ctx}):
            result = routing.compute_max_bytes("model-x")
        expected = int(ctx * routing._CONTEXT_HEADROOM * routing._BYTES_PER_TOKEN)
        assert result == expected

    def test_does_not_exceed_absolute_ceiling(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        # A very large context window should be capped
        with patch("worker.routing.litellm.get_model_info", return_value={"max_input_tokens": 10_000_000}):
            result = routing.compute_max_bytes("huge-context-model")
        assert result <= routing._MAX_PAYLOAD_BYTES

    def test_exactly_at_ceiling_is_capped(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        # Context window large enough that computed value exceeds cap
        with patch("worker.routing.litellm.get_model_info", return_value={"max_input_tokens": 5_000_000}):
            result = routing.compute_max_bytes("big-model")
        assert result == routing._MAX_PAYLOAD_BYTES

    def test_returns_int(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        with patch("worker.routing.litellm.get_model_info", return_value={"max_input_tokens": 128_000}):
            result = routing.compute_max_bytes("model-x")
        assert isinstance(result, int)

    def test_exception_from_get_model_info_returns_fallback(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.SHUNT_MAX_BYTES", None)
        with patch("worker.routing.litellm.get_model_info", side_effect=RuntimeError("err")):
            result = routing.compute_max_bytes("broken-model")
        assert result == routing._FALLBACK_MAX_BYTES


# ── model_for_text ────────────────────────────────────────────────────────────

class TestModelForText:
    """model_for_text() — returns config value directly."""

    def test_returns_config_trim_route_text(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "openai/gpt-4o")
        assert routing.model_for_text() == "openai/gpt-4o"

    def test_returns_empty_string_when_unset(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "")
        assert routing.model_for_text() == ""

    def test_returns_different_model(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "gemini/gemini-2.5-flash")
        assert routing.model_for_text() == "gemini/gemini-2.5-flash"


# ── model_for_pdf ─────────────────────────────────────────────────────────────

class TestModelForPdf:
    """model_for_pdf() — capability-gated PDF routing."""

    def test_returns_pdf_model_when_pdf_capable(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_PDF", "pdf-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_pdf_input": True}):
            result = routing.model_for_pdf()
        assert result == "pdf-model"

    def test_falls_back_to_text_model_when_pdf_route_unset(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_PDF", "")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_pdf_input": True}):
            result = routing.model_for_pdf()
        assert result == "text-model"

    def test_returns_empty_string_when_model_lacks_pdf_support(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_PDF", "no-pdf-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_pdf_input": False}):
            result = routing.model_for_pdf()
        assert result == ""

    def test_returns_empty_string_when_info_unavailable(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_PDF", "some-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={}):
            result = routing.model_for_pdf()
        assert result == ""

    def test_returns_empty_string_when_litellm_raises(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_PDF", "some-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", side_effect=Exception("boom")):
            result = routing.model_for_pdf()
        assert result == ""


# ── model_for_vision ──────────────────────────────────────────────────────────

class TestModelForVision:
    """model_for_vision() — capability-gated vision routing."""

    def test_returns_vision_model_when_vision_capable(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_VISION", "vision-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_vision": True}):
            result = routing.model_for_vision()
        assert result == "vision-model"

    def test_falls_back_to_text_model_when_vision_route_unset(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_VISION", "")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_vision": True}):
            result = routing.model_for_vision()
        assert result == "text-model"

    def test_returns_empty_string_when_model_lacks_vision(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_VISION", "no-vision-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_vision": False}):
            result = routing.model_for_vision()
        assert result == ""

    def test_returns_empty_string_when_info_unavailable(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_VISION", "some-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", return_value={}):
            result = routing.model_for_vision()
        assert result == ""

    def test_returns_empty_string_when_litellm_raises(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_VISION", "some-model")
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_TEXT", "text-model")
        with patch("worker.routing.litellm.get_model_info", side_effect=ValueError("err")):
            result = routing.model_for_vision()
        assert result == ""


# ── fallback_model ────────────────────────────────────────────────────────────

class TestFallbackModel:
    """fallback_model() — returns config value."""

    def test_returns_config_trim_route_fallback(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_FALLBACK", "fallback-model")
        assert routing.fallback_model() == "fallback-model"

    def test_returns_empty_string_when_unset(self, monkeypatch):
        monkeypatch.setattr("worker.routing.config.TRIM_ROUTE_FALLBACK", "")
        assert routing.fallback_model() == ""


# ── skip_temperature ──────────────────────────────────────────────────────────

class TestSkipTemperature:
    """skip_temperature() — True for reasoning models, True on unknown."""

    def test_true_when_model_supports_reasoning(self):
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_reasoning": True}):
            assert routing.skip_temperature("o1-mini") is True

    def test_false_when_model_does_not_support_reasoning(self):
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_reasoning": False}):
            assert routing.skip_temperature("gpt-4o") is False

    def test_false_when_supports_reasoning_absent_from_info(self):
        with patch("worker.routing.litellm.get_model_info", return_value={}):
            assert routing.skip_temperature("some-model") is False

    def test_true_when_litellm_raises(self):
        """Unknown model → safe default is True (don't send temperature)."""
        with patch("worker.routing.litellm.get_model_info", side_effect=Exception("unknown")):
            assert routing.skip_temperature("unknown-model") is True

    def test_true_when_litellm_raises_key_error(self):
        with patch("worker.routing.litellm.get_model_info", side_effect=KeyError("missing")):
            assert routing.skip_temperature("model-x") is True

    def test_supports_reasoning_truthy_value(self):
        """Non-boolean truthy value should still return True."""
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_reasoning": 1}):
            assert routing.skip_temperature("model-x") is True

    def test_supports_reasoning_falsy_value(self):
        """Non-boolean falsy value should still return False."""
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_reasoning": 0}):
            assert routing.skip_temperature("model-x") is False

    def test_returns_bool(self):
        with patch("worker.routing.litellm.get_model_info", return_value={"supports_reasoning": True}):
            result = routing.skip_temperature("model-x")
        assert isinstance(result, bool)
