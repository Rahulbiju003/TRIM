"""Tests for worker/dashboard.py — stats, cost, and HTML rendering."""
from __future__ import annotations

import json
import time

import pytest

from worker import dashboard


class TestModelCostUsd:
    """_model_cost_usd() pricing calculations."""

    def test_known_model_zero_tokens(self):
        cost = dashboard._model_cost_usd("gpt-4.1-nano", 0, 0)
        assert cost == pytest.approx(0.0)

    def test_known_model_nonzero_tokens(self):
        # gpt-4.1-nano: $0.10/M input, $0.40/M output
        cost = dashboard._model_cost_usd("gpt-4.1-nano", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.10 + 0.40)

    def test_known_model_input_only(self):
        cost = dashboard._model_cost_usd("gpt-4.1-nano", 1_000_000, 0)
        assert cost == pytest.approx(0.10)

    def test_known_model_output_only(self):
        cost = dashboard._model_cost_usd("gpt-4.1-nano", 0, 1_000_000)
        assert cost == pytest.approx(0.40)

    def test_free_model_zero_cost(self):
        cost = dashboard._model_cost_usd("openrouter/nex-agi/nex-n2.5-mini:free", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.0)

    def test_unknown_model_uses_fallback(self):
        # Unknown model: falls back to _FALLBACK_PRICE (1.0, 4.0)
        cost = dashboard._model_cost_usd("unknown-model-xyz", 1_000_000, 1_000_000)
        assert cost == pytest.approx(1.0 + 4.0)

    def test_case_insensitive_lookup(self):
        cost_lower = dashboard._model_cost_usd("gpt-4.1-nano", 1_000_000, 0)
        cost_upper = dashboard._model_cost_usd("GPT-4.1-NANO", 1_000_000, 0)
        assert cost_lower == cost_upper

    def test_whitespace_stripped(self):
        cost = dashboard._model_cost_usd("  gpt-4.1-nano  ", 1_000_000, 0)
        assert cost == pytest.approx(0.10)

    def test_partial_million_tokens(self):
        # 500K input at $0.10/M = $0.05
        cost = dashboard._model_cost_usd("gpt-4.1-nano", 500_000, 0)
        assert cost == pytest.approx(0.05)


class TestIsKnownModel:
    def test_known_model(self):
        assert dashboard._is_known_model("gpt-4.1-nano") is True

    def test_unknown_model(self):
        assert dashboard._is_known_model("fantasy-model-v99") is False

    def test_free_tier_known(self):
        assert dashboard._is_known_model("openrouter/nex-agi/nex-n2.5-mini:free") is True

    def test_case_insensitive(self):
        assert dashboard._is_known_model("GPT-4.1-NANO") is True


class TestComputeStatsEmpty:
    def test_empty_records_returns_zero_totals(self):
        stats = dashboard.compute_stats([])
        assert stats["total_delegations"] == 0
        assert stats["total_input_tokens"] == 0
        assert stats["total_output_tokens"] == 0
        assert stats["total_llm_cost_usd"] == pytest.approx(0.0)
        assert stats["avg_latency_ms"] == pytest.approx(0.0)
        assert stats["has_unknown_pricing"] is False
        assert stats["model_breakdown"] == {}
        assert stats["delegations_by_day"] == {}
        assert stats["recent"] == []


class TestComputeStats:
    """compute_stats() with real records."""

    def test_total_delegations(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert stats["total_delegations"] == 3

    def test_total_input_tokens(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert stats["total_input_tokens"] == 1100 + 1800 + 900

    def test_total_output_tokens(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert stats["total_output_tokens"] == 150 + 210 + 120

    def test_avg_latency_correct(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        expected = (280.0 + 410.0 + 190.0) / 3
        assert stats["avg_latency_ms"] == pytest.approx(expected, rel=0.01)

    def test_total_cost_positive(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert stats["total_llm_cost_usd"] >= 0

    def test_model_breakdown_has_both_models(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        breakdown = stats["model_breakdown"]
        assert "gpt-4.1-nano" in breakdown
        assert "gpt-4o-mini" in breakdown

    def test_model_breakdown_counts(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert stats["model_breakdown"]["gpt-4.1-nano"]["count"] == 2
        assert stats["model_breakdown"]["gpt-4o-mini"]["count"] == 1

    def test_model_breakdown_tokens(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        nano = stats["model_breakdown"]["gpt-4.1-nano"]
        assert nano["input_tokens"] == 1100 + 1800
        assert nano["output_tokens"] == 150 + 210

    def test_model_breakdown_known_price(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert stats["model_breakdown"]["gpt-4.1-nano"]["known_price"] is True
        assert stats["model_breakdown"]["gpt-4o-mini"]["known_price"] is True

    def test_unknown_model_sets_flag(self):
        records = [{
            "ts": time.time(), "file": "/x.py", "lines": 400,
            "latency_ms": 100.0, "input_tokens": 100, "output_tokens": 10,
            "mode": "subprocess", "model": "totally-unknown-model",
        }]
        stats = dashboard.compute_stats(records)
        assert stats["has_unknown_pricing"] is True

    def test_known_models_no_unknown_flag(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert stats["has_unknown_pricing"] is False

    def test_delegations_by_day_populated(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        assert len(stats["delegations_by_day"]) >= 1
        for day, count in stats["delegations_by_day"].items():
            assert len(day) == 10  # "YYYY-MM-DD"
            assert isinstance(count, int)

    def test_recent_at_most_20_records(self):
        now = time.time()
        records = [
            {"ts": now - i, "file": f"/f{i}.py", "lines": 400,
             "latency_ms": 100.0, "input_tokens": 100, "output_tokens": 10,
             "mode": "subprocess", "model": "gpt-4.1-nano"}
            for i in range(30)
        ]
        stats = dashboard.compute_stats(records)
        assert len(stats["recent"]) <= 20

    def test_recent_records_have_required_keys(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        required = {"ts_human", "file", "lines", "latency_ms", "input_tokens", "output_tokens", "cost_usd", "model", "mode"}
        for rec in stats["recent"]:
            assert required <= rec.keys()

    def test_recent_cost_usd_non_negative(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        for rec in stats["recent"]:
            assert rec["cost_usd"] >= 0

    def test_missing_ts_handled_gracefully(self):
        records = [{"file": "/x.py", "lines": 400, "latency_ms": 100.0,
                    "input_tokens": 100, "output_tokens": 10,
                    "mode": "subprocess", "model": "gpt-4.1-nano"}]
        stats = dashboard.compute_stats(records)
        assert stats["total_delegations"] == 1
        assert stats["delegations_by_day"] == {}

    def test_missing_tokens_default_to_zero(self):
        records = [{"ts": time.time(), "file": "/x.py", "lines": 400,
                    "latency_ms": 100.0, "mode": "subprocess", "model": "gpt-4.1-nano"}]
        stats = dashboard.compute_stats(records)
        assert stats["total_input_tokens"] == 0
        assert stats["total_output_tokens"] == 0


class TestReadMetrics:
    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("worker.config.SHUNT_METRICS_FILE", str(tmp_path / "nonexistent.jsonl"))
        records = dashboard.read_metrics()
        assert records == []

    def test_reads_valid_jsonl(self, isolated_metrics, sample_records):
        import worker.metrics as metrics_mod
        for r in sample_records:
            metrics_mod.log(
                file_path=r["file"],
                line_count=r["lines"],
                latency_ms=r["latency_ms"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                mode=r["mode"],
                model=r["model"],
            )
        records = dashboard.read_metrics()
        assert len(records) == 3

    def test_skips_malformed_lines(self, isolated_metrics):
        isolated_metrics.write_text('{"valid": 1}\nnot json\n{"also_valid": 2}\n')
        records = dashboard.read_metrics()
        assert len(records) == 2

    def test_skips_blank_lines(self, isolated_metrics):
        isolated_metrics.write_text('{"ts": 1}\n\n{"ts": 2}\n')
        records = dashboard.read_metrics()
        assert len(records) == 2

    def test_empty_file_returns_empty(self, isolated_metrics):
        isolated_metrics.write_text("")
        records = dashboard.read_metrics()
        assert records == []


class TestRenderHtml:
    def test_returns_string(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        html = dashboard.render_html(stats)
        assert isinstance(html, str)

    def test_contains_doctype(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        html = dashboard.render_html(stats)
        assert "<!DOCTYPE html>" in html

    def test_stats_json_embedded(self, sample_records):
        stats = dashboard.compute_stats(sample_records)
        html = dashboard.render_html(stats)
        assert str(stats["total_delegations"]) in html

    def test_worker_model_shown(self, sample_records, monkeypatch):
        monkeypatch.setattr("worker.config.WORKER_MODEL", "my-test-model")
        monkeypatch.setattr("worker.dashboard.config.WORKER_MODEL", "my-test-model")
        stats = dashboard.compute_stats(sample_records)
        html = dashboard.render_html(stats)
        assert "my-test-model" in html

    def test_embedded_json_is_parseable(self, sample_records):
        """stats_json embedded in the HTML must be valid JSON."""
        stats = dashboard.compute_stats(sample_records)
        html = dashboard.render_html(stats)
        # Extract the stats JSON from the script section
        start = html.index("const S = ") + len("const S = ")
        end = html.index(";\n", start)
        json.loads(html[start:end])  # must not raise

    def test_empty_stats_renders_without_error(self):
        stats = dashboard.compute_stats([])
        html = dashboard.render_html(stats)
        assert "TRIM" in html

    def test_worker_model_html_escaped(self, sample_records, monkeypatch):
        """Model name with HTML special chars must be escaped in the dashboard."""
        monkeypatch.setattr("worker.config.WORKER_MODEL", "<script>alert(1)</script>")
        monkeypatch.setattr("worker.dashboard.config.WORKER_MODEL", "<script>alert(1)</script>")
        stats = dashboard.compute_stats(sample_records)
        html = dashboard.render_html(stats)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_empty_worker_model_shows_placeholder(self, sample_records, monkeypatch):
        monkeypatch.setattr("worker.config.WORKER_MODEL", "")
        monkeypatch.setattr("worker.dashboard.config.WORKER_MODEL", "")
        stats = dashboard.compute_stats(sample_records)
        html = dashboard.render_html(stats)
        assert "(not set)" in html
