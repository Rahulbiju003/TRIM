"""BulkReaderMode — summarise a large file using the cheap worker LLM."""
from __future__ import annotations

import html
import time
from dataclasses import dataclass

from worker.backends.litellm_backend import CompletionResult, LiteLLMBackend
from worker import config, metrics

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


class BulkReaderMode:
    """Reads a file and asks the LLM to summarise it."""

    def __init__(self, backend: LiteLLMBackend | None = None) -> None:
        self.backend = backend or LiteLLMBackend()

    def run(self, file_path: str, question: str | None = None) -> BulkReadResult:
        """Read *file_path* and return a LLM-generated summary."""
        content = self._read_file(file_path)
        line_count = content.count("\n") + 1
        question = question or DEFAULT_QUESTION
        user_message = self._build_user_message(file_path, content, question)

        t0 = time.monotonic()
        result: CompletionResult = self.backend.complete(SYSTEM_PROMPT, user_message)
        latency_ms = (time.monotonic() - t0) * 1000

        metrics.log(
            file_path=file_path,
            line_count=line_count,
            latency_ms=latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            mode="subprocess",
            model=result.model,
        )

        return BulkReadResult(
            summary=result.content,
            file_path=file_path,
            line_count=line_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=latency_ms,
            model=result.model,
        )

    def run_from_content(
        self, file_path: str, content: str, question: str | None = None, mode: str = "http"
    ) -> BulkReadResult:
        """Like run() but caller provides file content (used in HTTP mode)."""
        line_count = content.count("\n") + 1
        question = question or DEFAULT_QUESTION
        user_message = self._build_user_message(file_path, content, question)

        t0 = time.monotonic()
        result: CompletionResult = self.backend.complete(SYSTEM_PROMPT, user_message)
        latency_ms = (time.monotonic() - t0) * 1000

        metrics.log(
            file_path=file_path,
            line_count=line_count,
            latency_ms=latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            mode=mode,
            model=result.model,
        )

        return BulkReadResult(
            summary=result.content,
            file_path=file_path,
            line_count=line_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=latency_ms,
            model=result.model,
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
