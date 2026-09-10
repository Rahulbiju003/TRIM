#!/usr/bin/env bash
# PreToolUse hook for the Bash tool.
# Intercepts: cat / head / tail / less / more on large files.
# Piped commands and other bash commands pass through unchanged.
#
# Hook input (stdin): JSON with tool_name and tool_input.command
# Hook output (stdout): JSON with additionalContext | exit 0 (pass-through)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    exit 0
fi

ENV_FILE="${PROJECT_ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a; source "$ENV_FILE"; set +a
fi

HOOK_JSON="$(cat)"

# Extract the bash command string
COMMAND="$(echo "$HOOK_JSON" | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
")"

[[ -z "$COMMAND" ]] && exit 0

# ── detect: simple read-only commands (no pipes, no redirects) ───────────────
# Must match: cat FILE, head FILE, tail FILE, less FILE, more FILE
# Must NOT match: cat file | grep ..., cat file > out, cat *.log (glob patterns)

"$PYTHON" - "$COMMAND" <<'EOF'
import re, sys

cmd = sys.argv[1]

# Reject piped or redirected commands
if '|' in cmd or '>' in cmd or '<' in cmd:
    sys.exit(1)

# Match: (cat|head|tail|less|more) [flags] SINGLE_FILE
pattern = r'^(cat|head|tail|less|more)\s+(?:-\S+\s+)*(\S+)$'
m = re.match(pattern, cmd.strip())
if not m:
    sys.exit(1)

file_path = m.group(2)
# Reject glob patterns
if '*' in file_path or '?' in file_path:
    sys.exit(1)

print(file_path)
EOF
FILE_PATH="$?"

# If the Python script exited non-zero, pass through
if (( FILE_PATH != 0 )); then
    exit 0
fi

# Re-run to get the actual file path string
FILE_PATH="$("$PYTHON" - "$COMMAND" <<'EOF'
import re, sys

cmd = sys.argv[1]
if '|' in cmd or '>' in cmd or '<' in cmd:
    sys.exit(1)

pattern = r'^(cat|head|tail|less|more)\s+(?:-\S+\s+)*(\S+)$'
m = re.match(pattern, cmd.strip())
if not m:
    sys.exit(1)

file_path = m.group(2)
if '*' in file_path or '?' in file_path:
    sys.exit(1)

print(file_path)
EOF
)" || exit 0

[[ -z "$FILE_PATH" || ! -f "$FILE_PATH" ]] && exit 0

# ── check line count ──────────────────────────────────────────────────────────
MIN_LINES="${SHUNT_MIN_LINES:-350}"
LINE_COUNT="$(wc -l < "$FILE_PATH" 2>/dev/null || echo 0)"
LINE_COUNT="${LINE_COUNT// /}"

if (( LINE_COUNT < MIN_LINES )); then
    exit 0
fi

MAX_BYTES="${SHUNT_MAX_BYTES:-400000}"
BYTE_COUNT="$(wc -c < "$FILE_PATH" 2>/dev/null || echo 0)"
BYTE_COUNT="${BYTE_COUNT// /}"

if (( BYTE_COUNT > MAX_BYTES )); then
    exit 0
fi

# ── delegate ──────────────────────────────────────────────────────────────────
WORKER_URL="${WORKER_URL:-}"

if [[ -n "$WORKER_URL" ]]; then
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
    SUMMARY="$("$PYTHON" -m worker bulk-read --file "$FILE_PATH" 2>/dev/null)" || { exit 0; }
fi

[[ -z "$SUMMARY" ]] && exit 0

"$PYTHON" -c "
import json, sys

summary = sys.argv[1]
file_path = sys.argv[2]
line_count = int(sys.argv[3])

context = (
    f'[shunt] Delegated bash read of {file_path} ({line_count} lines) to cheap LLM.\n'
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
