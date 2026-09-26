"""BulkReaderMode — summarise a large file using the cheap worker LLM.

Supports three paths:
  - Full summarization: entire file content sent to LLM (original behaviour).
  - Cache hit:         cached summary returned immediately, no LLM call.
  - Delta update:      previous summary + unified diff sent to LLM; summary patched cheaply.

Caching and delta mode are enabled by setting TRIM_CACHE_FILE in the environment.
When unset, behaviour is identical to the original — full summarization every read.

RTK (Rust Token Killer) is used as an optional Tier 0 pre-compressor in the full
summarization path. When available it reduces LLM input tokens at zero cost. The
cache key is always based on the original (uncompressed) content hash.
"""
from __future__ import annotations

import html
import time
from dataclasses import dataclass

from worker.backends.litellm_backend import CompletionResult, LiteLLMBackend
from worker import cache, config, differ, metrics, rtk as _rtk

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

# Maximum diff size (bytes) to send on the delta path.
# A 5% change in a 400 KB file is still ~20 KB of diff — large enough to
# exceed some model context windows. Fall back to full re-summarization above
# this threshold.
_MAX_DIFF_BYTES = 50_000


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
        """Read *file_path* from disk and return a summary.

        Cache hit  → returns immediately, no LLM call.
        Delta path → sends previous summary + diff to LLM (cheap update).
        Full path  → sends full file content to LLM (original behaviour).
        """
        content = self._read_file(file_path)
        return self._run_core(file_path, content, question, "subprocess", None)

    def run_from_content(
        self,
        file_path: str,
        content: str,
        question: str | None = None,
        mode: str = "http",
        model: str | None = None,
    ) -> BulkReadResult:
        """Like run() but caller provides file content (HTTP mode).

        model: optional override — binary handlers pass the vision/PDF model
               so the right LLM is used for the completion call.
        """
        return self._run_core(file_path, content, question, mode, model)

    # ── core logic (single implementation shared by both public methods) ───────

    def _run_core(
        self,
        file_path: str,
        content: str,
        question: str | None,
        mode: str,
        model: str | None,
    ) -> BulkReadResult:
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
            and len(diff_result.unified) <= _MAX_DIFF_BYTES
        )

        if use_delta:
            # stale and diff_result are guaranteed non-None by use_delta predicate
            user_message = self._build_delta_message(stale.summary, diff_result.unified)  # type: ignore[union-attr]
            t0 = time.monotonic()
            result: CompletionResult = self.backend.complete(DELTA_SYSTEM_PROMPT, user_message)
            latency_ms = (time.monotonic() - t0) * 1000
            cache.put(file_path, result.content, stale.delta_count + 1, content)  # type: ignore[union-attr]
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
        # RTK Tier 0: try to pre-compress. Cache key uses original content.
        rtk_result = _rtk.compress(file_path, content)
        content_for_llm = rtk_result.content if rtk_result is not None else content

        user_message = self._build_user_message(file_path, content_for_llm, question)
        t0 = time.monotonic()
        # Pass model= through complete(); if None, complete() uses self.backend.model
        # with context-window fallback. If set, bypasses fallback (explicit choice).
        result = self.backend.complete(SYSTEM_PROMPT, user_message, model=model)
        latency_ms = (time.monotonic() - t0) * 1000
        cache.put(file_path, result.content, 0, content)  # original content for delta
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
