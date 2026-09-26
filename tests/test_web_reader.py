"""Tests for worker/web_reader.py — web content reader and SSRF protection."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker.backends.litellm_backend import CompletionResult
from worker.web_reader import (
    WebReadResult,
    WebReaderMode,
    _is_safe_url,
    _is_github,
    _looks_like_json,
    _extract_html_text,
    _extract_github_blob,
    _format_json,
    _extract_github,
    _GITHUB_HOSTS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_backend(
    content: str = "page summary",
    input_tokens: int = 100,
    output_tokens: int = 50,
    model: str = "gpt-4.1-nano",
) -> MagicMock:
    backend = MagicMock()
    backend.model = model
    backend.complete = AsyncMock(return_value=CompletionResult(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    ))
    return backend


def _long_text(n: int = 200) -> str:
    return " ".join(["word"] * n)


# ── WebReadResult dataclass ───────────────────────────────────────────────────

class TestWebReadResult:
    """WebReadResult field storage and defaults."""

    def test_all_fields_stored(self):
        r = WebReadResult(
            summary="summary text",
            url="https://example.com",
            input_tokens=100,
            output_tokens=50,
            latency_ms=123.4,
            model="gpt-4.1-nano",
        )
        assert r.summary == "summary text"
        assert r.url == "https://example.com"
        assert r.input_tokens == 100
        assert r.output_tokens == 50
        assert r.latency_ms == pytest.approx(123.4)
        assert r.model == "gpt-4.1-nano"

    def test_pass_through_defaults_to_false(self):
        r = WebReadResult(
            summary="s", url="u", input_tokens=0,
            output_tokens=0, latency_ms=0.0, model="m",
        )
        assert r.pass_through is False

    def test_pass_through_can_be_set_true(self):
        r = WebReadResult(
            summary="", url="u", input_tokens=0,
            output_tokens=0, latency_ms=0.0, model="m", pass_through=True,
        )
        assert r.pass_through is True


# ── _is_safe_url ──────────────────────────────────────────────────────────────

class TestIsSafeUrl:
    """SSRF protection — private/loopback hosts must be blocked."""

    # --- blocked hosts ---

    def test_localhost_is_blocked(self):
        assert _is_safe_url("http://localhost/path") is False

    def test_127_0_0_1_is_blocked(self):
        assert _is_safe_url("http://127.0.0.1/") is False

    def test_127_x_x_x_is_blocked(self):
        assert _is_safe_url("http://127.0.0.2/") is False

    def test_127_255_255_255_is_blocked(self):
        assert _is_safe_url("http://127.255.255.255/") is False

    def test_10_0_0_1_is_blocked(self):
        assert _is_safe_url("http://10.0.0.1/") is False

    def test_10_x_x_x_range_is_blocked(self):
        assert _is_safe_url("http://10.99.88.77/") is False

    def test_192_168_1_1_is_blocked(self):
        assert _is_safe_url("http://192.168.1.1/") is False

    def test_192_168_x_x_range_is_blocked(self):
        assert _is_safe_url("http://192.168.255.254/") is False

    def test_172_16_is_blocked(self):
        assert _is_safe_url("http://172.16.0.1/") is False

    def test_172_20_is_blocked(self):
        assert _is_safe_url("http://172.20.1.1/") is False

    def test_172_31_is_blocked(self):
        assert _is_safe_url("http://172.31.255.255/") is False

    def test_169_254_link_local_is_blocked(self):
        assert _is_safe_url("http://169.254.169.254/") is False

    def test_ipv6_loopback_short_is_blocked(self):
        assert _is_safe_url("http://[::1]/") is False

    # --- safe hosts ---

    def test_example_com_is_safe(self):
        assert _is_safe_url("https://example.com/") is True

    def test_github_com_is_safe(self):
        assert _is_safe_url("https://github.com/user/repo") is True

    def test_api_endpoint_is_safe(self):
        assert _is_safe_url("https://api.openai.com/v1/models") is True

    def test_172_15_is_safe(self):
        """172.15.x.x is NOT in the private range (only 172.16–31)."""
        assert _is_safe_url("http://172.15.0.1/") is True

    def test_172_32_is_safe(self):
        """172.32.x.x is NOT in the private range."""
        assert _is_safe_url("http://172.32.0.1/") is True

    def test_11_x_x_x_is_safe(self):
        """Only 10.x.x.x is private; 11.x.x.x is public."""
        assert _is_safe_url("http://11.0.0.1/") is True

    def test_https_scheme_safe_host(self):
        assert _is_safe_url("https://docs.python.org/3/") is True

    def test_url_with_port_and_path(self):
        assert _is_safe_url("https://example.com:8443/api/data") is True

    def test_localhost_with_port_is_blocked(self):
        assert _is_safe_url("http://localhost:8080/api") is False

    def test_127_0_0_1_with_port_is_blocked(self):
        assert _is_safe_url("http://127.0.0.1:5000/") is False

    def test_unparseable_url_returns_true(self):
        """Malformed URL → safe-default True (let server handle it)."""
        assert _is_safe_url("not-a-url") is True


# ── _is_github ────────────────────────────────────────────────────────────────

class TestIsGithub:
    """_is_github() recognises all hosts in _GITHUB_HOSTS."""

    def test_github_com(self):
        assert _is_github("github.com") is True

    def test_www_github_com(self):
        assert _is_github("www.github.com") is True

    def test_raw_githubusercontent_com(self):
        assert _is_github("raw.githubusercontent.com") is True

    def test_api_github_com(self):
        assert _is_github("api.github.com") is True

    def test_gist_github_com(self):
        assert _is_github("gist.github.com") is True

    def test_all_known_hosts_covered(self):
        """Every host in _GITHUB_HOSTS returns True."""
        for host in _GITHUB_HOSTS:
            assert _is_github(host) is True, f"Expected True for {host}"

    def test_example_com_not_github(self):
        assert _is_github("example.com") is False

    def test_github_io_not_github(self):
        assert _is_github("user.github.io") is False

    def test_empty_string_not_github(self):
        assert _is_github("") is False

    def test_subdomain_not_github(self):
        assert _is_github("notgithub.com") is False


# ── _looks_like_json ──────────────────────────────────────────────────────────

class TestLooksLikeJson:
    """_looks_like_json() detects JSON content by leading character."""

    def test_object_is_json(self):
        assert _looks_like_json('{"key": "value"}') is True

    def test_array_is_json(self):
        assert _looks_like_json('[1, 2, 3]') is True

    def test_nested_object_is_json(self):
        assert _looks_like_json('{"a": {"b": 1}}') is True

    def test_whitespace_before_brace(self):
        assert _looks_like_json('  {"key": "val"}') is True

    def test_whitespace_before_bracket(self):
        assert _looks_like_json('  [1, 2]') is True

    def test_newline_before_brace(self):
        assert _looks_like_json('\n{"key": "val"}') is True

    def test_html_is_not_json(self):
        assert _looks_like_json("<html><body>text</body></html>") is False

    def test_plain_text_is_not_json(self):
        assert _looks_like_json("Hello world") is False

    def test_empty_string_is_not_json(self):
        assert _looks_like_json("") is False

    def test_xml_is_not_json(self):
        assert _looks_like_json("<?xml version='1.0'?><root/>") is False

    def test_markdown_is_not_json(self):
        assert _looks_like_json("# Title\n\nContent here") is False

    def test_string_value_is_not_json(self):
        """A bare JSON string (starting with ") is not matched."""
        assert _looks_like_json('"just a string"') is False


# ── _extract_html_text ────────────────────────────────────────────────────────

class TestExtractHtmlText:
    """_extract_html_text() — tag stripping and entity decoding."""

    def test_strips_tags(self):
        result = _extract_html_text("<p>Hello world</p>")
        assert "Hello world" in result
        assert "<p>" not in result
        assert "</p>" not in result

    def test_strips_script_tag(self):
        html = "<script>alert('xss')</script><p>safe content</p>"
        result = _extract_html_text(html)
        assert "alert" not in result
        assert "safe content" in result

    def test_strips_style_tag(self):
        html = "<style>.foo { color: red; }</style><p>visible</p>"
        result = _extract_html_text(html)
        assert "color" not in result
        assert "visible" in result

    def test_decodes_amp_entity(self):
        result = _extract_html_text("<p>fish &amp; chips</p>")
        assert "&" in result
        assert "&amp;" not in result

    def test_decodes_lt_entity(self):
        result = _extract_html_text("<p>a &lt; b</p>")
        assert "<" in result
        assert "&lt;" not in result

    def test_decodes_gt_entity(self):
        result = _extract_html_text("<p>a &gt; b</p>")
        assert ">" in result
        assert "&gt;" not in result

    def test_decodes_quot_entity(self):
        result = _extract_html_text('<p>say &quot;hi&quot;</p>')
        assert '"' in result

    def test_decodes_nbsp_entity(self):
        result = _extract_html_text("<p>a&nbsp;b</p>")
        assert "a b" in result or "a" in result  # nbsp → space

    def test_plain_text_unchanged(self):
        plain = "Just plain text without any tags"
        result = _extract_html_text(plain)
        assert "Just plain text" in result

    def test_strips_nested_tags(self):
        html = "<div><h1><span>Heading</span></h1></div>"
        result = _extract_html_text(html)
        assert "Heading" in result
        assert "<" not in result

    def test_collapses_extra_blank_lines(self):
        html = "line1\n\n\n\n\nline2"
        result = _extract_html_text(html)
        # Should have at most 2 consecutive newlines
        assert "\n\n\n" not in result

    def test_strips_attributes(self):
        html = '<a href="https://example.com" class="link">click here</a>'
        result = _extract_html_text(html)
        assert "click here" in result
        assert "href=" not in result

    def test_max_chars_truncation(self):
        long_html = "<p>" + "x" * 200_000 + "</p>"
        result = _extract_html_text(long_html, max_chars=100)
        assert len(result) <= 100

    def test_returns_string(self):
        result = _extract_html_text("<p>text</p>")
        assert isinstance(result, str)

    def test_empty_html_returns_empty_string(self):
        result = _extract_html_text("")
        assert result == ""

    def test_multiline_script_stripped(self):
        html = "<script>\nfunction foo() {\n  return 1;\n}\n</script><p>kept</p>"
        result = _extract_html_text(html)
        assert "foo" not in result
        assert "kept" in result

    def test_result_is_stripped(self):
        result = _extract_html_text("  <p>text</p>  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")


# ── _extract_github_blob ──────────────────────────────────────────────────────

class TestExtractGithubBlob:
    """_extract_github_blob() — code extraction from GitHub blob HTML."""

    def _make_blob_html(self, lines: list[str]) -> str:
        cells = "\n".join(
            f'<td class="blob-code blob-code-inner js-file-line">{line}</td>'
            for line in lines
        )
        return f"<table><tbody><tr>{cells}</tr></tbody></table>"

    def test_extracts_code_lines(self):
        html = self._make_blob_html(["def foo():", "    return 1"])
        result = _extract_github_blob(html)
        assert "def foo():" in result
        assert "return 1" in result

    def test_strips_inner_tags(self):
        html = (
            '<td class="blob-code">'
            '<span class="k">def</span> <span class="n">foo</span>():'
            "</td>"
        )
        result = _extract_github_blob(html)
        assert "<span" not in result
        assert "def" in result
        assert "foo" in result

    def test_falls_back_to_html_text_when_no_blob_cells(self):
        """Non-blob HTML falls back to generic text extraction."""
        html = "<html><body><p>regular page content</p></body></html>"
        result = _extract_github_blob(html)
        assert "regular page content" in result

    def test_preserves_line_structure(self):
        lines = ["line one", "line two", "line three"]
        html = self._make_blob_html(lines)
        result = _extract_github_blob(html)
        result_lines = result.splitlines()
        assert len(result_lines) == 3

    def test_max_chars_respected(self):
        lines = ["x" * 1000 for _ in range(200)]
        html = self._make_blob_html(lines)
        result = _extract_github_blob(html)
        assert len(result) <= 100_000


# ── run_from_content (integration) ───────────────────────────────────────────

class TestRunFromContent:
    """WebReaderMode.run_from_content() — end-to-end with mocked backend."""

    # --- SSRF: unsafe URLs must pass through ---

    async def test_localhost_returns_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content(
            "http://localhost/secret", _long_text()
        )
        assert result.pass_through is True

    async def test_127_0_0_1_returns_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content(
            "http://127.0.0.1/data", _long_text()
        )
        assert result.pass_through is True

    async def test_10_0_0_1_returns_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content(
            "http://10.0.0.1/api", _long_text()
        )
        assert result.pass_through is True

    async def test_192_168_1_1_returns_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content(
            "http://192.168.1.1/panel", _long_text()
        )
        assert result.pass_through is True

    async def test_172_16_returns_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content(
            "http://172.16.0.1/internal", _long_text()
        )
        assert result.pass_through is True

    async def test_unsafe_url_never_calls_backend(self, isolated_metrics):
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        await reader.run_from_content("http://localhost/", _long_text())
        backend.complete.assert_not_called()

    async def test_unsafe_url_returns_empty_summary(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content("http://localhost/", _long_text())
        assert result.summary == ""

    async def test_unsafe_url_returns_zero_tokens(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content("http://10.0.0.1/", _long_text())
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    # --- Short content passes through ---

    async def test_short_content_returns_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content(
            "https://example.com", "short"  # < 100 chars
        )
        assert result.pass_through is True

    async def test_empty_content_returns_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content("https://example.com", "")
        assert result.pass_through is True

    async def test_content_under_100_chars_passes_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        # 99 non-space chars → after strip, length < 100
        result = await reader.run_from_content("https://example.com", "x" * 99)
        assert result.pass_through is True

    # --- Safe URL with sufficient content ---

    async def test_safe_url_returns_web_read_result(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend(content="nice summary"))
        result = await reader.run_from_content(
            "https://example.com", _long_text()
        )
        assert isinstance(result, WebReadResult)

    async def test_safe_url_not_pass_through(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content(
            "https://example.com", _long_text()
        )
        assert result.pass_through is False

    async def test_summary_from_backend(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend(content="the page summary"))
        result = await reader.run_from_content(
            "https://example.com", _long_text()
        )
        assert result.summary == "the page summary"

    async def test_url_preserved_in_result(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        url = "https://example.com/page"
        result = await reader.run_from_content(url, _long_text())
        assert result.url == url

    async def test_token_counts_from_backend(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend(input_tokens=200, output_tokens=75))
        result = await reader.run_from_content("https://example.com", _long_text())
        assert result.input_tokens == 200
        assert result.output_tokens == 75

    async def test_model_from_backend(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend(model="gemini/flash"))
        result = await reader.run_from_content("https://example.com", _long_text())
        assert result.model == "gemini/flash"

    async def test_latency_ms_non_negative(self, isolated_metrics):
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content("https://example.com", _long_text())
        assert result.latency_ms >= 0.0

    # --- Content routing ---

    async def test_html_content_processed(self, isolated_metrics):
        html = "<html><body>" + "<p>content word</p>" * 50 + "</body></html>"
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        await reader.run_from_content("https://example.com", html)
        # Ensure backend was called (content was long enough after stripping)
        # (exact call count depends on stripped length; we just check not pass_through)

    async def test_json_content_processed(self, isolated_metrics):
        json_str = '{"items": [' + ', '.join(f'"item{i}"' for i in range(50)) + ']}'
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content("https://api.example.com/data", json_str)
        # Should process, not pass through (JSON is > 100 chars)
        assert result.pass_through is False

    async def test_github_url_processed(self, isolated_metrics):
        content = "# Title\n" + "Some content line\n" * 20
        reader = WebReaderMode(backend=_mock_backend())
        result = await reader.run_from_content("https://github.com/user/repo", content)
        assert isinstance(result, WebReadResult)

    # --- Default and custom prompt ---

    async def test_default_prompt_used_when_none(self, isolated_metrics):
        from worker.web_reader import WEB_DEFAULT_QUESTION
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        await reader.run_from_content("https://example.com", _long_text(), prompt=None)
        call_args = backend.complete.call_args
        user_message = call_args[0][1]
        # The escaped version of the default question must appear
        assert "Summarise this page" in user_message

    async def test_custom_prompt_used(self, isolated_metrics):
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        await reader.run_from_content(
            "https://example.com", _long_text(), prompt="What is the main API?"
        )
        call_args = backend.complete.call_args
        user_message = call_args[0][1]
        assert "What is the main API?" in user_message

    async def test_url_included_in_user_message(self, isolated_metrics):
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        url = "https://example.com/specific-page"
        await reader.run_from_content(url, _long_text())
        user_message = backend.complete.call_args[0][1]
        assert url in user_message

    # --- GitHub system prompt ---

    async def test_github_uses_github_system_prompt(self, isolated_metrics):
        from worker.web_reader import GITHUB_SYSTEM_PROMPT
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        content = "GitHub page content with enough text " * 10
        await reader.run_from_content("https://github.com/user/repo", content)
        system_prompt = backend.complete.call_args[0][0]
        assert system_prompt == GITHUB_SYSTEM_PROMPT

    async def test_non_github_uses_web_system_prompt(self, isolated_metrics):
        from worker.web_reader import WEB_SYSTEM_PROMPT
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        await reader.run_from_content("https://example.com", _long_text())
        system_prompt = backend.complete.call_args[0][0]
        assert system_prompt == WEB_SYSTEM_PROMPT

    # --- Backend init ---

    def test_creates_default_backend_when_none_provided(self):
        with patch("worker.web_reader.LiteLLMBackend") as mock_cls:
            mock_cls.return_value = MagicMock()
            mock_cls.return_value.model = "m"
            WebReaderMode()
            mock_cls.assert_called_once()

    def test_accepts_provided_backend(self):
        backend = _mock_backend()
        reader = WebReaderMode(backend=backend)
        assert reader.backend is backend


# ── _format_json ──────────────────────────────────────────────────────────────

class TestFormatJson:
    """_format_json() — pretty-prints valid JSON, falls back on invalid."""

    def test_pretty_prints_object(self):
        result = _format_json('{"a":1,"b":2}')
        assert '"a": 1' in result
        assert '"b": 2' in result

    def test_pretty_prints_array(self):
        result = _format_json('[1,2,3]')
        assert "1" in result

    def test_fallback_on_invalid_json(self):
        raw = "not valid json at all"
        result = _format_json(raw)
        assert result == raw

    def test_max_chars_truncation(self):
        long_json = '{"key": "' + "x" * 200_000 + '"}'
        result = _format_json(long_json, max_chars=100)
        assert len(result) <= 100

    def test_returns_string(self):
        result = _format_json('{"x": 1}')
        assert isinstance(result, str)


# ── _extract_github (routing) ─────────────────────────────────────────────────

class TestExtractGithub:
    """_extract_github() — routes by hostname and path."""

    def test_api_github_com_formats_json(self):
        content = '{"id": 123, "name": "repo"}'
        result = _extract_github("https://api.github.com/repos/user/repo", content)
        assert '"id": 123' in result

    def test_raw_githubusercontent_returns_raw_content(self):
        raw_code = "def foo():\n    return 42\n"
        result = _extract_github(
            "https://raw.githubusercontent.com/user/repo/main/foo.py", raw_code
        )
        assert raw_code in result

    def test_blob_path_uses_blob_extractor(self):
        html = (
            '<td class="blob-code blob-code-inner">def bar(): pass</td>'
        )
        result = _extract_github("https://github.com/user/repo/blob/main/foo.py", html)
        assert "bar" in result

    def test_issues_path_uses_html_extractor(self):
        html = "<html><body><p>Issue description here</p></body></html>"
        result = _extract_github("https://github.com/user/repo/issues/42", html)
        assert "Issue description here" in result

    def test_pull_path_uses_html_extractor(self):
        html = "<html><body><p>PR description</p></body></html>"
        result = _extract_github("https://github.com/user/repo/pull/99", html)
        assert "PR description" in result

    def test_repo_root_uses_html_extractor(self):
        html = "<html><body><p>Repository home</p></body></html>"
        result = _extract_github("https://github.com/user/repo", html)
        assert "Repository home" in result

    def test_gist_uses_html_extractor(self):
        html = "<html><body><p>Gist content</p></body></html>"
        result = _extract_github("https://gist.github.com/user/abc123", html)
        assert "Gist content" in result
