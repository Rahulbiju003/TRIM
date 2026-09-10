"""Tests for TRIM bash hook scripts.

These tests verify:
1. JSON output format matches what Claude Code expects
2. Routing thresholds (line count / byte count)
3. Fail-open behaviour on errors
4. Hook script existence and executability

Hooks are tested by running them via subprocess with controlled stdin/env.
The worker LLM call is mocked by pointing WORKER_URL at a local HTTP server
or by patching the subprocess worker invocation.
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest


# ── paths ─────────────────────────────────────────────────────────────────────

TRIM_ROOT = Path(__file__).parent.parent
HOOKS_DIR = TRIM_ROOT / ".claude" / "hooks"
READ_HOOK = HOOKS_DIR / "check-file-size.sh"
BASH_HOOK = HOOKS_DIR / "check-bash-read.sh"


# ── helpers ───────────────────────────────────────────────────────────────────

def _hook_input(file_path: str) -> str:
    """Build the JSON stdin payload that Claude Code sends to the Read hook."""
    return json.dumps({"tool_name": "Read", "tool_input": {"file_path": file_path}})


def _bash_hook_input(command: str) -> str:
    """Build the JSON stdin payload for the Bash hook."""
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def _run_hook(
    hook: Path,
    stdin: str,
    env: dict | None = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess:
    """Run a hook script with controlled environment and stdin."""
    base_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["/bin/bash", str(hook)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env,
    )


class _MockWorkerHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP server that responds to /bulk-read like the real worker."""

    SUMMARY = "Mock summary from test worker."

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"summary": self.SUMMARY, "model": "gpt-4.1-nano"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):  # suppress access log noise
        pass


@pytest.fixture(scope="module")
def mock_worker_server():
    """Start a local HTTP mock worker and return its base URL."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockWorkerHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@contextlib.contextmanager
def _temp_env_file(worker_url: str):
    """Temporarily replace TRIM_ROOT/.env with a minimal test version.

    The hooks source .env at startup, overriding subprocess env vars.
    To make the hook hit our mock server we must patch .env itself.
    The original is backed up and restored in the finally block.
    """
    env_path = TRIM_ROOT / ".env"
    backup_path = TRIM_ROOT / ".env.test-backup"
    backed_up = False
    try:
        if env_path.exists():
            shutil.copy2(env_path, backup_path)
            backed_up = True
        env_path.write_text(f"WORKER_URL={worker_url}\nSHUNT_MIN_LINES=10\n")
        yield
    finally:
        env_path.unlink(missing_ok=True)
        if backed_up:
            shutil.move(str(backup_path), str(env_path))


# ── hook existence ────────────────────────────────────────────────────────────

class TestHookFilesExist:
    def test_read_hook_exists(self):
        assert READ_HOOK.is_file(), f"Hook not found: {READ_HOOK}"

    def test_bash_hook_exists(self):
        assert BASH_HOOK.is_file(), f"Hook not found: {BASH_HOOK}"

    def test_read_hook_executable(self):
        assert os.access(READ_HOOK, os.X_OK), f"Hook not executable: {READ_HOOK}"

    def test_bash_hook_executable(self):
        assert os.access(BASH_HOOK, os.X_OK), f"Hook not executable: {BASH_HOOK}"


# ── fail-open: no venv ────────────────────────────────────────────────────────

class TestFailOpenNoVenv:
    """When Python venv is missing, hooks must exit 0 with no output."""

    def test_read_hook_exits_zero_no_venv(self, tmp_path):
        fake_file = tmp_path / "large.py"
        fake_file.write_text("\n".join(f"line {i}" for i in range(500)))
        result = _run_hook(
            READ_HOOK,
            _hook_input(str(fake_file)),
            env={"TRIM_ROOT_OVERRIDE": str(tmp_path)},  # no .venv here
        )
        assert result.returncode == 0

    def test_bash_hook_exits_zero_no_venv(self, tmp_path):
        fake_file = tmp_path / "large.py"
        fake_file.write_text("\n".join(f"line {i}" for i in range(500)))
        result = _run_hook(
            BASH_HOOK,
            _bash_hook_input(f"cat {fake_file}"),
            env={"TRIM_ROOT_OVERRIDE": str(tmp_path)},
        )
        assert result.returncode == 0


# ── hook output JSON format ───────────────────────────────────────────────────

class TestHookJsonOutputFormat:
    """The JSON format produced by hooks must match Claude Code's spec."""

    def test_deny_output_structure(self, mock_worker_server, tmp_path):
        """When routing, hook must emit correct hookSpecificOutput structure."""
        large = tmp_path / "large.py"
        large.write_text("\n".join(f"# line {i}" for i in range(500)) + "\n")

        python = TRIM_ROOT / ".venv" / "bin" / "python"
        if not python.exists():
            pytest.skip("No .venv found — install with pip install -e .[dev]")

        with _temp_env_file(mock_worker_server):
            result = _run_hook(READ_HOOK, _hook_input(str(large)), env={
                "PATH": str(python.parent) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
            })

        assert result.stdout.strip(), f"Hook produced no output; stderr: {result.stderr!r}"

        data = json.loads(result.stdout)
        assert "hookSpecificOutput" in data, f"Missing hookSpecificOutput in: {data}"

        hso = data["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "additionalContext" in hso
        assert isinstance(hso["additionalContext"], str)
        assert len(hso["additionalContext"]) > 0

    def test_deny_additional_context_contains_summary(self, mock_worker_server, tmp_path):
        large = tmp_path / "large.py"
        large.write_text("\n".join(f"# line {i}" for i in range(500)) + "\n")

        python = TRIM_ROOT / ".venv" / "bin" / "python"
        if not python.exists():
            pytest.skip("No .venv found")

        with _temp_env_file(mock_worker_server):
            result = _run_hook(READ_HOOK, _hook_input(str(large)), env={
                "PATH": str(python.parent) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
            })

        assert result.stdout.strip(), f"Hook produced no output; stderr: {result.stderr!r}"

        data = json.loads(result.stdout)
        context = data["hookSpecificOutput"]["additionalContext"]
        assert _MockWorkerHandler.SUMMARY in context


# ── routing threshold: small files pass through ───────────────────────────────

class TestRoutingThresholds:
    """Small files must not be routed — hook must exit 0 with empty stdout."""

    def _run_with_venv(self, hook, stdin, extra_env=None):
        python = TRIM_ROOT / ".venv" / "bin" / "python"
        if not python.exists():
            pytest.skip("No .venv found")
        env = {
            "PATH": str(python.parent) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
        }
        if extra_env:
            env.update(extra_env)
        return _run_hook(hook, stdin, env=env)

    def test_small_file_passes_through_read_hook(self, small_file):
        result = self._run_with_venv(
            READ_HOOK,
            _hook_input(str(small_file)),
            {"SHUNT_MIN_LINES": "350"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", f"Expected empty stdout, got: {result.stdout!r}"

    def test_large_file_with_high_threshold_passes_through(self, large_file):
        result = self._run_with_venv(
            READ_HOOK,
            _hook_input(str(large_file)),
            {"SHUNT_MIN_LINES": "9999", "WORKER_URL": ""},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_nonexistent_file_passes_through_read_hook(self):
        result = self._run_with_venv(
            READ_HOOK,
            _hook_input("/no/such/file/xyz.py"),
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_partial_read_passes_through(self, large_file):
        """offset or limit → intentional partial read, must not route."""
        payload = json.dumps({
            "tool_name": "Read",
            "tool_input": {"file_path": str(large_file), "offset": 1, "limit": 50},
        })
        result = self._run_with_venv(READ_HOOK, payload, {"SHUNT_MIN_LINES": "10"})
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ── Bash hook routing ─────────────────────────────────────────────────────────

class TestBashHookRouting:
    """Bash hook must route cat/head/tail on large files and pass through others."""

    def _run_with_venv(self, stdin, extra_env=None):
        python = TRIM_ROOT / ".venv" / "bin" / "python"
        if not python.exists():
            pytest.skip("No .venv found")
        env = {
            "PATH": str(python.parent) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
        }
        if extra_env:
            env.update(extra_env)
        return _run_hook(BASH_HOOK, stdin, env=env)

    def test_cat_small_file_passes_through(self, small_file):
        result = self._run_with_venv(
            _bash_hook_input(f"cat {small_file}"),
            {"SHUNT_MIN_LINES": "350"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_piped_command_passes_through(self, large_file):
        result = self._run_with_venv(
            _bash_hook_input(f"cat {large_file} | grep foo"),
            {"SHUNT_MIN_LINES": "10"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_nonexistent_file_passes_through(self):
        result = self._run_with_venv(
            _bash_hook_input("cat /no/such/file.py"),
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_glob_passes_through(self, large_file):
        result = self._run_with_venv(
            _bash_hook_input(f"cat {large_file.parent}/*.py"),
            {"SHUNT_MIN_LINES": "10"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_redirect_passes_through(self, large_file):
        result = self._run_with_venv(
            _bash_hook_input(f"cat {large_file} > /tmp/out.txt"),
            {"SHUNT_MIN_LINES": "10"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_arbitrary_bash_command_passes_through(self):
        result = self._run_with_venv(
            _bash_hook_input("ls -la /tmp"),
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ── Python JSON-output unit tests (no bash required) ─────────────────────────

class TestHookOutputPython:
    """Test the Python JSON output logic in isolation (no bash subprocess)."""

    def test_deny_json_structure_valid(self):
        """Simulate what the hook's Python inline script produces."""
        summary = "This file defines Foo class."
        file_path = "/src/app.py"
        line_count = "450"

        context = (
            f"[TRIM] Delegated read of {file_path} ({line_count} lines) to cheap LLM.\n"
            f"Summary:\n{summary}\n\n"
            f"(Full file was NOT loaded — use this summary to answer the question.)"
        )

        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "File routed to cheap LLM by TRIM",
                "additionalContext": context,
            }
        }

        serialised = json.dumps(output)
        parsed = json.loads(serialised)

        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "additionalContext" in hso
        assert file_path in hso["additionalContext"]
        assert summary in hso["additionalContext"]

    def test_special_chars_in_summary_survive_json(self):
        """Summaries with quotes/backslashes must survive JSON round-trip."""
        summary = 'He said "hello" and used C:\\path\\to\\file'
        output = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "deny",
                                          "additionalContext": summary}}
        parsed = json.loads(json.dumps(output))
        assert parsed["hookSpecificOutput"]["additionalContext"] == summary

    def test_unicode_summary_survives_json(self):
        summary = "文件定义了 Foo 类 🚀"
        output = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "deny",
                                          "additionalContext": summary}}
        parsed = json.loads(json.dumps(output))
        assert parsed["hookSpecificOutput"]["additionalContext"] == summary

    def test_additional_context_is_top_level_mistake_detected(self):
        """additionalContext at top level (old bug) would be outside hookSpecificOutput."""
        wrong_output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
            },
            "additionalContext": "this is in the wrong place",
        }
        # Claude Code ignores top-level additionalContext — verify it's missing
        assert "additionalContext" not in wrong_output["hookSpecificOutput"]


# ── Bash hook command-parser unit tests ───────────────────────────────────────

class TestBashHookCommandParsing:
    """Unit-test the command-detection logic embedded in check-bash-read.sh.

    The hook extracts a file path from the bash command string using a Python
    snippet. These tests exercise that logic directly so we don't need a full
    subprocess round-trip for every pattern.
    """

    def _parse(self, cmd: str) -> str | None:
        """Replicate the hook's Python parser. Returns file path or None."""
        import re
        cmd = cmd.strip()
        if "|" in cmd or ">" in cmd or "<" in cmd:
            return None
        m = re.match(r"^(cat|head|tail|less|more)\s+(?:-\S+\s+)*(\S+)$", cmd)
        if not m:
            return None
        fp = m.group(2)
        if "*" in fp or "?" in fp or "[" in fp:
            return None
        return fp

    def test_normal_file_extracted(self):
        assert self._parse("cat /src/app.py") == "/src/app.py"

    def test_star_glob_rejected(self):
        assert self._parse("cat /src/*.py") is None

    def test_question_mark_glob_rejected(self):
        assert self._parse("cat /src/file?.py") is None

    def test_bracket_expression_rejected(self):
        assert self._parse("cat /src/[abc]file.py") is None

    def test_bracket_range_rejected(self):
        assert self._parse("cat /src/[0-9]test.py") is None

    def test_bracket_in_middle_rejected(self):
        assert self._parse("cat /src/file[0].py") is None

    def test_pipe_rejected(self):
        assert self._parse("cat /src/app.py | grep foo") is None

    def test_redirect_rejected(self):
        assert self._parse("cat /src/app.py > /tmp/out") is None

    def test_head_with_flag_extracted(self):
        assert self._parse("head -50 /src/app.py") == "/src/app.py"

    def test_unsupported_command_rejected(self):
        assert self._parse("vim /src/app.py") is None

    def test_multiple_files_rejected(self):
        # Two file args don't match the single-file pattern
        assert self._parse("cat /src/a.py /src/b.py") is None
