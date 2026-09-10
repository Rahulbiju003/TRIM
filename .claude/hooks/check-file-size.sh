#!/usr/bin/env bash
# PreToolUse hook for the Read tool.
# Routes large file reads to the cheap worker LLM.
# On any error: exit 0 (fail-open) so Claude reads the file normally.
#
# Hook input (stdin): JSON with tool_name and tool_input keys.
# Hook output (stdout): JSON with additionalContext and permissionDecision.

set -euo pipefail

# ── locate the shunt venv ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Hooks live in <project>/.claude/hooks/ → project root is two levels up
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    # venv not found → fail-open
    exit 0
fi

# ── load .env if present (subprocess mode needs API keys) ────────────────────
ENV_FILE="${PROJECT_ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

# ── read hook JSON from stdin ─────────────────────────────────────────────────
HOOK_JSON="$(cat)"

# Extract file_path, offset, limit from tool_input
FILE_PATH="$(echo "$HOOK_JSON" | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('file_path', ''))
")"

OFFSET="$(echo "$HOOK_JSON" | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('offset', ''))
")"

LIMIT="$(echo "$HOOK_JSON" | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('limit', ''))
")"

# Pass-through if no file path
[[ -z "$FILE_PATH" ]] && exit 0

# Pass-through if offset or limit specified (partial reads are intentional)
[[ -n "$OFFSET" || -n "$LIMIT" ]] && exit 0

# Pass-through if file does not exist
[[ ! -f "$FILE_PATH" ]] && exit 0

# ── check line count ──────────────────────────────────────────────────────────
MIN_LINES="${SHUNT_MIN_LINES:-350}"
LINE_COUNT="$(wc -l < "$FILE_PATH" 2>/dev/null || echo 0)"
LINE_COUNT="${LINE_COUNT// /}"  # strip whitespace

if (( LINE_COUNT < MIN_LINES )); then
    exit 0  # small file — let Claude read it directly
fi

# ── check byte size ───────────────────────────────────────────────────────────
MAX_BYTES="${SHUNT_MAX_BYTES:-400000}"
BYTE_COUNT="$(wc -c < "$FILE_PATH" 2>/dev/null || echo 0)"
BYTE_COUNT="${BYTE_COUNT// /}"

if (( BYTE_COUNT > MAX_BYTES )); then
    # File is too large to pipe; just pass through with a warning
    exit 0
fi

# ── delegate to worker ────────────────────────────────────────────────────────
WORKER_URL="${WORKER_URL:-}"

if [[ -n "$WORKER_URL" ]]; then
    # ── HTTP mode: send file content to server ────────────────────────────────
    CONTENT="$(cat "$FILE_PATH")"
    PAYLOAD="$(printf '%s' "$CONTENT" | "$PYTHON" -c "
import json, sys
content = sys.stdin.read()
print(json.dumps({'file_path': '${FILE_PATH}', 'content': content}))
")"

    RESPONSE="$(curl -sf \
        -X POST "${WORKER_URL}/bulk-read" \
        -H 'Content-Type: application/json' \
        -d "$PAYLOAD" \
        --max-time "${SHUNT_TIMEOUT_SECONDS:-45}" 2>/dev/null)" || { exit 0; }

    SUMMARY="$(echo "$RESPONSE" | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('summary', ''))
")"
else
    # ── Subprocess mode: invoke worker directly ───────────────────────────────
    SUMMARY="$("$PYTHON" -m worker bulk-read --file "$FILE_PATH" 2>/dev/null)" || { exit 0; }
fi

[[ -z "$SUMMARY" ]] && exit 0

# ── emit hook JSON response ───────────────────────────────────────────────────
"$PYTHON" -c "
import json, sys

summary = sys.argv[1]
file_path = sys.argv[2]
line_count = int(sys.argv[3])

context = (
    f'[shunt] Delegated read of {file_path} ({line_count} lines) to cheap LLM.\n'
    f'Summary:\n{summary}\n'
    f'(Full file NOT loaded into context — use this summary instead.)'
)

print(json.dumps({
    'hookSpecificOutput': {
        'permissionDecision': 'allow',
    },
    'additionalContext': context,
    'suppressToolUse': True,
}))
" "$SUMMARY" "$FILE_PATH" "$LINE_COUNT"
