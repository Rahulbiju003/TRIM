"""Tests for worker/server.py — FastAPI endpoints."""
from __future__ import annotations

from unittest.mock import patch

import worker.server as server_module


# ── /health ──────────────────────────────────────────────────────────────────

class TestHealth:
    def test_status_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_model_field_present(self, client, monkeypatch):
        monkeypatch.setattr("worker.config.WORKER_MODEL", "test-model-x")
        monkeypatch.setattr(server_module.config, "WORKER_MODEL", "test-model-x")
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "model" in resp.json()

    def test_no_auth_required(self, auth_client):
        """Health endpoint must be accessible without auth even when key is set."""
        resp = auth_client.get("/health")
        assert resp.status_code == 200


# ── /dashboard ────────────────────────────────────────────────────────────────

class TestDashboard:
    def test_returns_html(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_html_contains_trim(self, client):
        resp = client.get("/dashboard")
        assert "TRIM" in resp.text

    def test_no_auth_required(self, auth_client):
        """Dashboard must be open even when auth is configured."""
        resp = auth_client.get("/dashboard")
        assert resp.status_code == 200


# ── /api/metrics ─────────────────────────────────────────────────────────────

class TestApiMetrics:
    def test_returns_json(self, client):
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "records" in body
        assert "count" in body

    def test_count_matches_records_length(self, client):
        resp = client.get("/api/metrics")
        body = resp.json()
        assert body["count"] == len(body["records"])

    def test_no_auth_required(self, auth_client):
        resp = auth_client.get("/api/metrics")
        assert resp.status_code == 200


# ── POST /bulk-read ───────────────────────────────────────────────────────────

class TestBulkRead:
    def _payload(self, file_path="/src/app.py", content="x\n" * 10, question=None):
        body = {"file_path": file_path, "content": content}
        if question is not None:
            body["question"] = question
        return body

    def test_success_200(self, client):
        resp = client.post("/bulk-read", json=self._payload())
        assert resp.status_code == 200

    def test_response_has_summary(self, client, mock_result):
        resp = client.post("/bulk-read", json=self._payload())
        assert resp.json()["summary"] == mock_result.summary

    def test_reader_called_with_correct_file_path(self, client, mock_result):
        import worker.server as srv
        with patch.object(srv, "_reader") as mock_reader:
            mock_reader.run_from_content.return_value = mock_result
            client.post("/bulk-read", json=self._payload(file_path="/my/file.py"))
            call_kwargs = mock_reader.run_from_content.call_args[1]
            assert call_kwargs.get("file_path") == "/my/file.py"

    def test_response_has_line_count(self, client, mock_result):
        resp = client.post("/bulk-read", json=self._payload())
        assert "line_count" in resp.json()

    def test_response_has_token_fields(self, client):
        resp = client.post("/bulk-read", json=self._payload())
        body = resp.json()
        assert "input_tokens" in body
        assert "output_tokens" in body

    def test_response_has_latency_ms(self, client):
        resp = client.post("/bulk-read", json=self._payload())
        assert "latency_ms" in resp.json()

    def test_response_has_model(self, client):
        resp = client.post("/bulk-read", json=self._payload())
        assert "model" in resp.json()

    def test_question_forwarded_to_reader(self, client, mock_result):
        import worker.server as srv
        with patch.object(srv, "_reader") as mock_reader:
            mock_reader.run_from_content.return_value = mock_result
            client.post("/bulk-read", json=self._payload(question="What does it do?"))
            call_kwargs = mock_reader.run_from_content.call_args[1]
            assert call_kwargs.get("question") == "What does it do?"

    def test_null_question_accepted(self, client):
        resp = client.post("/bulk-read", json={
            "file_path": "/x.py", "content": "code", "question": None
        })
        assert resp.status_code == 200

    def test_missing_file_path_422(self, client):
        resp = client.post("/bulk-read", json={"content": "code"})
        assert resp.status_code == 422

    def test_missing_content_422(self, client):
        resp = client.post("/bulk-read", json={"file_path": "/x.py"})
        assert resp.status_code == 422

    def test_backend_error_returns_500(self, client, mock_result):
        import worker.server as srv
        with patch.object(srv, "_reader") as mock_reader:
            mock_reader.run_from_content.side_effect = RuntimeError("boom")
            resp = client.post("/bulk-read", json=self._payload())
        assert resp.status_code == 500

    def test_provider_rate_limit_returns_429(self, client):
        """Provider RateLimitError must surface as 429, not 500, for better monitoring."""
        import litellm.exceptions
        import worker.server as srv
        with patch.object(srv, "_reader") as mock_reader:
            mock_reader.run_from_content.side_effect = litellm.exceptions.RateLimitError(
                message="Too many requests", llm_provider="openai", model="gpt-4.1-nano"
            )
            resp = client.post("/bulk-read", json=self._payload())
        assert resp.status_code == 429

    def test_provider_rate_limit_detail_is_safe(self, client):
        """The 429 detail must not leak provider error internals."""
        import litellm.exceptions
        import worker.server as srv
        with patch.object(srv, "_reader") as mock_reader:
            mock_reader.run_from_content.side_effect = litellm.exceptions.RateLimitError(
                message="secret_key_leak", llm_provider="openai", model="gpt-4.1-nano"
            )
            resp = client.post("/bulk-read", json=self._payload())
        assert "secret_key_leak" not in resp.text

    def test_mode_is_http(self, client, mock_result):
        import worker.server as srv
        with patch.object(srv, "_reader") as mock_reader:
            mock_reader.run_from_content.return_value = mock_result
            client.post("/bulk-read", json=self._payload())
            call_kwargs = mock_reader.run_from_content.call_args[1]
            assert call_kwargs.get("mode") == "http"


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_no_key_required_when_not_configured(self, client):
        """When TRIM_API_KEY is not set, requests go through without any header."""
        resp = client.post("/bulk-read", json={"file_path": "/x.py", "content": "hi"})
        assert resp.status_code == 200

    def test_correct_key_allowed(self, auth_client):
        resp = auth_client.post(
            "/bulk-read",
            json={"file_path": "/x.py", "content": "hi"},
            headers={"X-TRIM-Key": "test-secret"},
        )
        assert resp.status_code == 200

    def test_missing_key_header_rejected(self, auth_client):
        resp = auth_client.post("/bulk-read", json={"file_path": "/x.py", "content": "hi"})
        assert resp.status_code == 401

    def test_wrong_key_rejected(self, auth_client):
        resp = auth_client.post(
            "/bulk-read",
            json={"file_path": "/x.py", "content": "hi"},
            headers={"X-TRIM-Key": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_empty_key_rejected(self, auth_client):
        resp = auth_client.post(
            "/bulk-read",
            json={"file_path": "/x.py", "content": "hi"},
            headers={"X-TRIM-Key": ""},
        )
        assert resp.status_code == 401

    def test_health_open_with_auth_configured(self, auth_client):
        resp = auth_client.get("/health")
        assert resp.status_code == 200

    def test_dashboard_open_with_auth_configured(self, auth_client):
        resp = auth_client.get("/dashboard")
        assert resp.status_code == 200

    def test_metrics_open_with_auth_configured(self, auth_client):
        resp = auth_client.get("/api/metrics")
        assert resp.status_code == 200


# ── _SlidingWindowRateLimiter (unit) ─────────────────────────────────────────

class TestSlidingWindowRateLimiter:
    def test_rpm_zero_always_allows(self):
        limiter = server_module._SlidingWindowRateLimiter(0)
        for _ in range(100):
            assert limiter.is_allowed()

    def test_negative_rpm_always_allows(self):
        limiter = server_module._SlidingWindowRateLimiter(-1)
        assert limiter.is_allowed()

    def test_allows_up_to_limit(self):
        limiter = server_module._SlidingWindowRateLimiter(3)
        assert limiter.is_allowed()
        assert limiter.is_allowed()
        assert limiter.is_allowed()

    def test_rejects_beyond_limit(self):
        limiter = server_module._SlidingWindowRateLimiter(3)
        limiter.is_allowed()
        limiter.is_allowed()
        limiter.is_allowed()
        assert not limiter.is_allowed()

    def test_window_expires_old_requests(self):
        from unittest.mock import patch as _patch
        limiter = server_module._SlidingWindowRateLimiter(2)
        with _patch("worker.server.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            limiter.is_allowed()
            limiter.is_allowed()
            assert not limiter.is_allowed()
            # Advance 61 seconds — old requests expire from the sliding window
            mock_time.monotonic.return_value = 61.0
            assert limiter.is_allowed()

    def test_partial_window_expiry(self):
        """Only requests outside the 60s window should be evicted."""
        from unittest.mock import patch as _patch
        limiter = server_module._SlidingWindowRateLimiter(2)
        with _patch("worker.server.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            limiter.is_allowed()          # t=0
            mock_time.monotonic.return_value = 30.0
            limiter.is_allowed()          # t=30
            # At t=61, only the t=0 entry expires; t=30 is still in window
            mock_time.monotonic.return_value = 61.0
            assert limiter.is_allowed()   # now 1 slot free (t=0 expired)
            assert not limiter.is_allowed()  # t=30 still in window, limit=2 hit

    def test_thread_safety(self):
        import threading
        limiter = server_module._SlidingWindowRateLimiter(10)
        results = []
        lock = threading.Lock()

        def _call():
            result = limiter.is_allowed()
            with lock:
                results.append(result)

        threads = [threading.Thread(target=_call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 10


# ── Rate limit — HTTP endpoint ────────────────────────────────────────────────

class TestRateLimitEndpoint:
    def _payload(self):
        return {"file_path": "/x.py", "content": "hi"}

    def test_429_returned_when_limit_exceeded(self, client):
        with patch.object(server_module, "_rate_limiter") as mock_rl:
            mock_rl.is_allowed.return_value = False
            resp = client.post("/bulk-read", json=self._payload())
        assert resp.status_code == 429

    def test_429_detail_mentions_limit(self, client):
        with patch.object(server_module, "_rate_limiter") as mock_rl:
            mock_rl.is_allowed.return_value = False
            resp = client.post("/bulk-read", json=self._payload())
        assert "limit" in resp.json()["detail"].lower()

    def test_allowed_when_within_limit(self, client):
        with patch.object(server_module, "_rate_limiter") as mock_rl:
            mock_rl.is_allowed.return_value = True
            resp = client.post("/bulk-read", json=self._payload())
        assert resp.status_code == 200

    def test_health_not_affected_by_rate_limiter(self, client):
        """Rate limiter must not gate /health."""
        with patch.object(server_module, "_rate_limiter") as mock_rl:
            mock_rl.is_allowed.return_value = False
            resp = client.get("/health")
        assert resp.status_code == 200

    def test_dashboard_not_affected_by_rate_limiter(self, client):
        with patch.object(server_module, "_rate_limiter") as mock_rl:
            mock_rl.is_allowed.return_value = False
            resp = client.get("/dashboard")
        assert resp.status_code == 200

    def test_default_rate_limiter_allows_requests(self, client):
        """With SHUNT_RATE_LIMIT_RPM=0 (default), all requests pass through."""
        # _rate_limiter is already initialised with rpm=0 in the module
        resp = client.post("/bulk-read", json=self._payload())
        assert resp.status_code == 200
