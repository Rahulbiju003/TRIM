"""Web content reader for TRIM.

Processes content fetched from URLs — strips HTML, routes GitHub URLs,
formats JSON — then summarizes with the LLM using the caller's prompt as
the question (not a generic "summarize this file" prompt).
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from worker.backends.litellm_backend import CompletionResult, LiteLLMBackend
from worker import config, metrics

WEB_SYSTEM_PROMPT = """\
You are a precise technical content analyst. You receive the content of a web page \
and a question about it. Produce a compact, accurate answer so the calling agent \
never needs to fetch the page itself.

Rules:
- Lead with the direct answer; no preamble.
- Use bullet points for lists.
- Preserve exact identifiers, version numbers, code snippets, and URLs where relevant.
- If something is not in the content, say so explicitly — never hallucinate.
- Keep your response under 800 tokens unless the question demands more detail.\
"""

GITHUB_SYSTEM_PROMPT = """\
You are a precise technical analyst specialising in GitHub content. \
You receive GitHub page content and a question. Produce a compact, accurate answer.

Rules:
- Lead with the direct answer; no preamble.
- For issues/PRs: include title, status, key discussion points, resolution if any.
- For code files: list purpose, public API (functions/classes with signatures), key dependencies.
- For search results: list the most relevant matches with file paths and context.
- For repo pages: include description, primary language, key files, README highlights.
- Preserve exact identifiers, version numbers, and file paths.
- Never hallucinate — if something is not in the content, say so.
- Keep your response under 800 tokens.\
"""

WEB_DEFAULT_QUESTION = (
    "Summarise this page: its purpose, main topics, key information, "
    "and any important links or data. Be concise."
)


@dataclass
class WebReadResult:
    summary: str
    url: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str
    pass_through: bool = False


class WebReaderMode:
    """Summarises web content fetched from a URL."""

    def __init__(self, backend: LiteLLMBackend | None = None) -> None:
        self.backend = backend or LiteLLMBackend()

    def run_from_content(
        self,
        url: str,
        content: str,
        prompt: str | None = None,
    ) -> WebReadResult:
        """Summarise web content.

        url:     The source URL (used for GitHub routing and displayed in summary).
        content: Raw text content — HTML, JSON, markdown, or plain text.
        prompt:  The question from Claude's WebFetch call (used as the LLM question).
        """
        question = prompt or WEB_DEFAULT_QUESTION
        hostname = urlparse(url).hostname or ""

        if _is_github(hostname):
            text = _extract_github(url, content)
            system = GITHUB_SYSTEM_PROMPT
        elif _looks_like_json(content):
            text = _format_json(content)
            system = WEB_SYSTEM_PROMPT
        else:
            text = _extract_html_text(content)
            system = WEB_SYSTEM_PROMPT

        if not text or len(text.strip()) < 100:
            return WebReadResult(
                summary="", url=url, input_tokens=0, output_tokens=0,
                latency_ms=0.0, model=self.backend.model, pass_through=True,
            )

        safe_q = _html.escape(question)
        user_message = f"URL: {url}\n\n{text}\n\n<question>{safe_q}</question>"

        t0 = time.monotonic()
        result: CompletionResult = self.backend.complete(system, user_message)
        latency_ms = (time.monotonic() - t0) * 1000

        metrics.log(
            file_path=url,
            line_count=len(text.splitlines()),
            latency_ms=latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            mode="http",
            model=result.model,
            cache_hit=False,
            delta=False,
        )

        return WebReadResult(
            summary=result.content,
            url=url,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=latency_ms,
            model=result.model,
        )


# ── Content type helpers ──────────────────────────────────────────────────────

_GITHUB_HOSTS = frozenset({
    "github.com", "www.github.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "gist.github.com",
})


def _is_github(hostname: str) -> bool:
    return hostname in _GITHUB_HOSTS


def _looks_like_json(content: str) -> bool:
    s = content.lstrip()
    return s.startswith("{") or s.startswith("[")


# ── HTML text extraction ──────────────────────────────────────────────────────

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE  = re.compile(r"<style[^>]*>.*?</style>",  re.DOTALL | re.IGNORECASE)
_TAG_RE    = re.compile(r"<[^>]+>")
_SPACE_RE  = re.compile(r"[ \t]{2,}")
_BLANK_RE  = re.compile(r"\n{3,}")
_ENTITIES  = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&nbsp;": " ",
    "&#x27;": "'", "&#x2F;": "/",
}


def _decode_entities(text: str) -> str:
    for ent, ch in _ENTITIES.items():
        text = text.replace(ent, ch)
    return text


def _extract_html_text(content: str, max_chars: int = 100_000) -> str:
    """Strip HTML and return readable text."""
    text = _SCRIPT_RE.sub(" ", content)
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _decode_entities(text)
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()[:max_chars]


# ── JSON formatting ───────────────────────────────────────────────────────────

def _format_json(content: str, max_chars: int = 80_000) -> str:
    try:
        return json.dumps(json.loads(content), indent=2, ensure_ascii=False)[:max_chars]
    except Exception:
        return content[:max_chars]


# ── GitHub-specific extraction ────────────────────────────────────────────────

def _extract_github(url: str, content: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path

    if host == "api.github.com":
        return _format_json(content)

    if host == "raw.githubusercontent.com":
        return content[:100_000]

    if "/blob/" in path or "/raw/" in path:
        return _extract_github_blob(content)

    if "/issues/" in path or "/pull/" in path:
        return _extract_html_text(content)

    # repo page, search, gist, other — generic extraction
    return _extract_html_text(content)


def _extract_github_blob(content: str) -> str:
    """Extract code lines from a GitHub blob HTML page."""
    # GitHub renders code in <td class="blob-code …"> cells
    cells = re.findall(
        r'<td[^>]*class="[^"]*blob-code[^"]*"[^>]*>(.*?)</td>',
        content, re.DOTALL,
    )
    if cells:
        lines = [_TAG_RE.sub("", cell).rstrip() for cell in cells]
        return "\n".join(lines)[:100_000]
    return _extract_html_text(content)
