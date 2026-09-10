#!/usr/bin/env bash
# TRIM — PreToolUse hook for the Bash tool.
# Intercepts: cat / head / tail / less / more on large files.
# Piped commands and anything else pass through unchanged.
#
# Hook input  (stdin):  JSON with tool_name and tool_input.command
# Hook output (stdout): JSON with permissionDecision=deny + additionalContext
#                       OR nothing (exit 0 = pass-through).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

[[ ! -x "$PYTHON" ]] && exit 0

ENV_FILE="${PROJECT_ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a; source "$ENV_FILE"; set +a
fi

HOOK_JSON="$(cat)"

COMMAND="$("$PYTHON" -c "
import json, sys
d = json.loads(sys.stdin.read())
print(d.get('tool_input', {}).get('command', ''))
" <<< "$HOOK_JSON")" || exit 0

[[ -z "$COMMAND" ]] && exit 0

# ── detect simple read-only commands (no pipes, no redirects, no globs) ───────
FILE_PATH="$("$PYTHON" - "$COMMAND" << 'PYEOF'
import re, sys

cmd = sys.argv[1]

# Reject piped or redirected commands — pass through for complex usage
if '|' in cmd or '>' in cmd or '<' in cmd:
    sys.exit(1)

# Match: (cat|head|tail|less|more) [optional-flags] SINGLE_FILE
pattern = r'^(cat|head|tail|less|more)\s+(?:-\S+\s+)*(\S+)$'
m = re.match(pattern, cmd.strip())
if not m:
    sys.exit(1)

file_path = m.group(2)
# Reject globs — pass through
if '*' in file_path or '?' in file_path:
    sys.exit(1)

print(file_path)
PYEOF
)" || exit 0

[[ -z "$FILE_PATH" || ! -f "$FILE_PATH" ]] && exit 0

# ── routing checks ────────────────────────────────────────────────────────────
MIN_LINES="${SHUNT_MIN_LINES:-350}"
MAX_BYTES="${SHUNT_MAX_BYTES:-400000}"

LINE_COUNT="$(wc -l < "$FILE_PATH" 2>/dev/null | tr -d ' ')" || exit 0
# wc -l counts newlines; files without a trailing newline lose one count
if [[ "$LINE_COUNT" -gt 0 ]] && [[ "$(tail -c 1 "$FILE_PATH" 2>/dev/null)" != $'\n' ]]; then
    LINE_COUNT=$(( LINE_COUNT + 1 ))
fi
BYTE_COUNT="$(wc -c < "$FILE_PATH" 2>/dev/null | tr -d ' ')" || exit 0

if (( LINE_COUNT < MIN_LINES )); then exit 0; fi
if (( BYTE_COUNT > MAX_BYTES )); then exit 0; fi

# ── delegate to worker ────────────────────────────────────────────────────────
WORKER_URL="${WORKER_URL:-}"
TRIM_API_KEY="${TRIM_API_KEY:-}"

if [[ -n "$WORKER_URL" ]]; then
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
    SUMMARY="$("$PYTHON" -m worker bulk-read --file "$FILE_PATH" 2>/dev/null)" || exit 0
fi

[[ -z "$SUMMARY" ]] && exit 0

# ── emit hook response ────────────────────────────────────────────────────────
"$PYTHON" -c "
import json, sys
summary   = sys.argv[1]
file_path = sys.argv[2]
line_count = sys.argv[3]

context = (
    f'[TRIM] Delegated bash read of {file_path} ({line_count} lines) to cheap LLM.\n'
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
