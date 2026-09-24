"""BulkReaderMode — summarise a large file using the cheap worker LLM.

Supports three paths:
  - Full summarization: entire file content sent to LLM (original behaviour).
  - Cache hit:         cached summary returned immediately, no LLM call.
  - Delta update:      previous summary + unified diff sent to LLM; summary patched cheaply.

Caching and delta mode are enabled by setting TRIM_CACHE_FILE in the environment.
When unset, behaviour is identical to the original — full summarization every read.
"""
from __future__ import annotations

import html
import time
from dataclasses import dataclass

from worker.backends.litellm_backend import CompletionResult, LiteLLMBackend
from worker import cache, config, differ, metrics

SYSTEM_PROMPT = """\
You are a precise technical file analyst. You receive the full content of a \
source-code file and a question about it. Your task is to produce a compact, \
accurate answer so the calling agent never needs to read the file itself.

Rules:
- Lead with the direct answer; no preamble.
- Use bullet points for lists of items (functions, classes, imports, etc.).
- Preserve exact identifiers, line numbers, and signatures where relevant.
- If something is not in the file, say so explicitly — never hallucinate.
- Keep your response under 600 tokens unless the question demands more detail.\
"""

DELTA_SYSTEM_PROMPT = """\
You are a precise technical file analyst maintaining an accurate summary of a \
source-code file across edits.

You will receive:
1. A previous summary of the file.
2. A unified diff showing exactly what changed.

Update the summary to reflect the changes. Rules:
- Only modify the parts of the summary that are affected by the diff.
- Preserve accurate parts of the previous summary verbatim.
- Lead with the updated summary directly — no preamble or explanation of what changed.
- Preserve exact identifiers, line numbers, and signatures where relevant.
- Never hallucinate — if the diff is ambiguous, note the uncertainty.
- Keep your response under 600 tokens.\
"""

DEFAULT_QUESTION = (
    "Summarise this file: list its purpose, public classes/functions "
    "(with signatures), key constants, and any notable side-effects or "
    "dependencies. Be concise."
)


@dataclass
class BulkReadResult:
    summary: str
    file_path: str
    line_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str
    cache_hit: bool = False
    delta: bool = False


class BulkReaderMode:
    """Reads a file and asks the LLM to summarise it."""

    def __init__(self, backend: LiteLLMBackend | None = None) -> None:
        self.backend = backend or LiteLLMBackend()

    def run(self, file_path: str, question: str | None = None) -> BulkReadResult:
        """Read *file_path* and return a summary.

        Cache hit  → returns immediately, no LLM call.
        Delta path → sends previous summary + diff to LLM (cheap update).
        Full path  → sends full file content to LLM (original behaviour).
        """
        content = self._read_file(file_path)
        line_count = len(content.splitlines())
        question = question or DEFAULT_QUESTION

        # ── cache check ───────────────────────────────────────────────────────
        entry = cache.get(file_path)
        if entry is not None:
            metrics.log(
                file_path=file_path, line_count=line_count, latency_ms=0.0,
                input_tokens=0, output_tokens=0, mode="subprocess",
                model=self.backend.model, cache_hit=True, delta=False,
            )
            return BulkReadResult(
                summary=entry.summary, file_path=file_path, line_count=line_count,
                input_tokens=0, output_tokens=0, latency_ms=0.0,
                model=self.backend.model, cache_hit=True, delta=False,
            )

        # ── delta check ───────────────────────────────────────────────────────
        stale = cache.get_stale(file_path)
        diff_result = differ.compute(file_path, content, stale.content) if stale else None
        use_delta = (
            stale is not None
            and diff_result is not None
            and stale.delta_count < config.TRIM_MAX_DELTA_COUNT
            and diff_result.changed_ratio <= config.TRIM_DELTA_THRESHOLD
        )

        if use_delta:
            assert stale is not None and diff_result is not None
            user_message = self._build_delta_message(stale.summary, diff_result.unified)
            t0 = time.monotonic()
            result: CompletionResult = self.backend.complete(DELTA_SYSTEM_PROMPT, user_message)
            latency_ms = (time.monotonic() - t0) * 1000
            cache.put(file_path, result.content, stale.delta_count + 1, content)
            metrics.log(
                file_path=file_path, line_count=line_count, latency_ms=latency_ms,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                mode="subprocess", model=result.model, cache_hit=False, delta=True,
            )
            return BulkReadResult(
                summary=result.content, file_path=file_path, line_count=line_count,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                latency_ms=latency_ms, model=result.model, cache_hit=False, delta=True,
            )

        # ── full summarization (original behaviour) ───────────────────────────
        user_message = self._build_user_message(file_path, content, question)
        t0 = time.monotonic()
        result = self.backend.complete(SYSTEM_PROMPT, user_message)
        latency_ms = (time.monotonic() - t0) * 1000
        cache.put(file_path, result.content, 0, content)
        metrics.log(
            file_path=file_path, line_count=line_count, latency_ms=latency_ms,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            mode="subprocess", model=result.model, cache_hit=False, delta=False,
        )
        return BulkReadResult(
            summary=result.content, file_path=file_path, line_count=line_count,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            latency_ms=latency_ms, model=result.model, cache_hit=False, delta=False,
        )

    def run_from_content(
        self, file_path: str, content: str, question: str | None = None, mode: str = "http"
    ) -> BulkReadResult:
        """Like run() but caller provides file content (HTTP mode).

        Applies the same cache hit / delta / full summarization logic.
        """
        line_count = len(content.splitlines())
        question = question or DEFAULT_QUESTION

        # ── cache check ───────────────────────────────────────────────────────
        entry = cache.get(file_path)
        if entry is not None:
            metrics.log(
                file_path=file_path, line_count=line_count, latency_ms=0.0,
                input_tokens=0, output_tokens=0, mode=mode,
                model=self.backend.model, cache_hit=True, delta=False,
            )
            return BulkReadResult(
                summary=entry.summary, file_path=file_path, line_count=line_count,
                input_tokens=0, output_tokens=0, latency_ms=0.0,
                model=self.backend.model, cache_hit=True, delta=False,
            )

        # ── delta check ───────────────────────────────────────────────────────
        stale = cache.get_stale(file_path)
        diff_result = differ.compute(file_path, content, stale.content) if stale else None
        use_delta = (
            stale is not None
            and diff_result is not None
            and stale.delta_count < config.TRIM_MAX_DELTA_COUNT
            and diff_result.changed_ratio <= config.TRIM_DELTA_THRESHOLD
        )

        if use_delta:
            assert stale is not None and diff_result is not None
            user_message = self._build_delta_message(stale.summary, diff_result.unified)
            t0 = time.monotonic()
            result: CompletionResult = self.backend.complete(DELTA_SYSTEM_PROMPT, user_message)
            latency_ms = (time.monotonic() - t0) * 1000
            cache.put(file_path, result.content, stale.delta_count + 1, content)
            metrics.log(
                file_path=file_path, line_count=line_count, latency_ms=latency_ms,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                mode=mode, model=result.model, cache_hit=False, delta=True,
            )
            return BulkReadResult(
                summary=result.content, file_path=file_path, line_count=line_count,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                latency_ms=latency_ms, model=result.model, cache_hit=False, delta=True,
            )

        # ── full summarization ────────────────────────────────────────────────
        user_message = self._build_user_message(file_path, content, question)
        t0 = time.monotonic()
        result = self.backend.complete(SYSTEM_PROMPT, user_message)
        latency_ms = (time.monotonic() - t0) * 1000
        cache.put(file_path, result.content, 0, content)
        metrics.log(
            file_path=file_path, line_count=line_count, latency_ms=latency_ms,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            mode=mode, model=result.model, cache_hit=False, delta=False,
        )
        return BulkReadResult(
            summary=result.content, file_path=file_path, line_count=line_count,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            latency_ms=latency_ms, model=result.model, cache_hit=False, delta=False,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _read_file(path: str) -> str:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    @staticmethod
    def _build_user_message(file_path: str, content: str, question: str) -> str:
        # Escape file_path into the XML attribute (prevents attribute injection via
        # paths like: /foo" injected="true).
        # Escape question content (prevents tag injection via </question> in input).
        # File content is left unescaped — the model needs raw source text.
        safe_path = html.escape(file_path, quote=True)
        safe_question = html.escape(question)
        return (
            f'<file path="{safe_path}">\n'
            f"{content}\n"
            f"</file>\n\n"
            f"<question>{safe_question}</question>"
        )

    @staticmethod
    def _build_delta_message(previous_summary: str, unified_diff: str) -> str:
        return (
            f"<previous_summary>\n{previous_summary}\n</previous_summary>\n\n"
            f"<diff>\n{unified_diff}\n</diff>"
        )
