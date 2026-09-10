#!/usr/bin/env bash
# TRIM — PreToolUse hook for the Read tool.
# Routes large file reads to the cheap worker LLM.
# On any error: exit 0 (fail-open) so Claude reads the file normally.
#
# Hook input  (stdin):  JSON with tool_name and tool_input keys.
# Hook output (stdout): JSON with permissionDecision=deny + additionalContext
#                       OR nothing (exit 0 = pass-through).

set -euo pipefail

# ── locate the TRIM venv ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Hooks live in <trim>/.claude/hooks/ → TRIM root is two levels up
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    exit 0  # venv not found → fail-open
fi

# ── load .env (subprocess mode needs API keys) ────────────────────────────────
ENV_FILE="${PROJECT_ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

# ── read hook JSON from stdin ─────────────────────────────────────────────────
HOOK_JSON="$(cat)"

# Extract file_path, offset, limit safely via Python
read -r FILE_PATH OFFSET LIMIT < <("$PYTHON" - <<PYEOF
import json, sys
d = json.loads($(printf '%s' "$HOOK_JSON" | "$PYTHON" -c "import json,sys; print(repr(sys.stdin.read()))"))
ti = d.get('tool_input', {})
print(ti.get('file_path', ''), ti.get('offset', ''), ti.get('limit', ''))
PYEOF
) || exit 0

[[ -z "$FILE_PATH" || ! -f "$FILE_PATH" ]] && exit 0
[[ -n "$OFFSET" || -n "$LIMIT" ]] && exit 0  # partial reads are intentional

# ── routing checks ────────────────────────────────────────────────────────────
MIN_LINES="${SHUNT_MIN_LINES:-350}"
MAX_BYTES="${SHUNT_MAX_BYTES:-400000}"

LINE_COUNT="$(wc -l < "$FILE_PATH" 2>/dev/null | tr -d ' ')" || exit 0
# wc -l counts newlines; files without a trailing newline lose one count
if [[ "$LINE_COUNT" -gt 0 ]] && [[ "$(tail -c 1 "$FILE_PATH" 2>/dev/null)" != $'\n' ]]; then
    LINE_COUNT=$(( LINE_COUNT + 1 ))
fi
BYTE_COUNT="$(wc -c < "$FILE_PATH" 2>/dev/null | tr -d ' ')" || exit 0

if (( LINE_COUNT < MIN_LINES )); then exit 0; fi   # small file → pass through
if (( BYTE_COUNT > MAX_BYTES )); then exit 0; fi   # too large to pipe → pass through

# ── delegate to worker ────────────────────────────────────────────────────────
WORKER_URL="${WORKER_URL:-}"
TRIM_API_KEY="${TRIM_API_KEY:-}"

if [[ -n "$WORKER_URL" ]]; then
    # HTTP mode: Python reads the file directly (safe, no shell quoting issues)
    PAYLOAD="$("$PYTHON" -c "
import json, sys
fp = sys.argv[1]
with open(fp, encoding='utf-8', errors='replace') as f:
    content = f.read()
print(json.dumps({'file_path': fp, 'content': content}))
" "$FILE_PATH")" || exit 0

    AUTH_HEADER=""
    [[ -n "$TRIM_API_KEY" ]] && AUTH_HEADER="-H X-TRIM-Key:${TRIM_API_KEY}"

    # SC2086: intentional word-split so -H and the value become two args for curl
    # shellcheck disable=SC2086
    RESPONSE="$(printf '%s\n' "$PAYLOAD" | curl -sf \
        -X POST "${WORKER_URL}/bulk-read" \
        -H 'Content-Type: application/json' \
        --data-binary @- \
        ${AUTH_HEADER:+$AUTH_HEADER} \
        --max-time "${SHUNT_TIMEOUT_SECONDS:-45}" 2>/dev/null)" || exit 0

    SUMMARY="$("$PYTHON" -c "
import json, sys
d = json.loads(sys.stdin.read())
print(d.get('summary', ''))
" <<< "$RESPONSE")" || exit 0
else
    # Subprocess mode: invoke worker directly
    SUMMARY="$("$PYTHON" -m worker bulk-read --file "$FILE_PATH" 2>/dev/null)" || exit 0
fi

[[ -z "$SUMMARY" ]] && exit 0

# ── emit hook response: deny tool execution, inject summary as context ─────────
# permissionDecision=deny prevents Claude from loading the full file.
# additionalContext injects the summary into Claude's conversation.
"$PYTHON" -c "
import json, sys
summary  = sys.argv[1]
file_path = sys.argv[2]
line_count = sys.argv[3]

context = (
    f'[TRIM] Delegated read of {file_path} ({line_count} lines) to cheap LLM.\n'
    f'Summary:\n{summary}\n\n'
    f'(Full file was NOT loaded — use this summary to answer the question.)'
)

print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'File routed to cheap LLM by TRIM',
        'additionalContext': context,
    },
}))
" "$SUMMARY" "$FILE_PATH" "$LINE_COUNT"
