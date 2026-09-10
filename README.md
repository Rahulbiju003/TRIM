# TRIM
**Token Routing Intelligence Middleware**

TRIM is a harness for Claude Code that intercepts expensive file reads and routes them to a cheaper LLM, returning a compact summary instead of loading the full file into context. It sits between Claude and your codebase as a transparent proxy — Claude never knows the difference, but your token bill does.
---

## How it works

```
Claude Code
    │
    │  tries to Read large-file.java (800 lines)
    ▼
PreToolUse Hook (TRIM)
    │
    ├─ file < 350 lines? → pass through, Claude reads normally
    │
    └─ file ≥ 350 lines? → send to cheap LLM (Podman worker)
                               │
                               ▼
                         Worker (LiteLLM)
                         openrouter/gemma-3n  ← free tier
                               │
                               ▼
                         Summary injected as additionalContext
                               │
                               ▼
                         Claude sees the summary, not the raw file
```

TRIM also intercepts `cat`, `head`, `tail` calls on large files via the Bash hook.

---

## Quick Start (Podman — recommended)

### 1. Clone and configure

```bash
git clone https://github.com/Rahulbiju003/TRIM.git
cd TRIM
cp .env.example .env
```

Edit `.env` and add your key:

```bash
OPENROUTER_API_KEY=sk-or-...   # free tier at openrouter.ai
```

Everything else works with the defaults.

### 2. Start the worker container

```bash
podman-compose up -d
```

Verify it is running:

```bash
curl http://localhost:8080/health
# {"status":"ok","model":"openrouter/google/gemma-3n-e4b-it:free"}
```

### 3. Wire TRIM into a Claude Code project

Run this from the TRIM directory, pointing at your project:

```bash
./setup.sh --install /path/to/your/project
```

This generates two hook scripts in your project's `.claude/hooks/` and patches
`.claude/settings.json`. The hooks use only `curl` — no Python needed on your host.

### 4. Open Claude Code in your project

TRIM is now active. Large file reads are routed automatically. Check
`/tmp/trim-metrics.jsonl` for a record of every delegation.

---

## Setup options

| Mode | When to use | How |
|------|------------|-----|
| **Podman (local)** | Solo dev, default | `podman-compose up -d` then `./setup.sh --install` |
| **Podman (remote)** | Shared team server | `WORKER_URL=https://your-server ./setup.sh --install` |
| **Subprocess** | No container runtime | `./setup.sh --local --install` (uses local venv) |

### Podman remote (team setup)

Deploy the container on any server reachable by your team, then on each developer machine:

```bash
WORKER_URL=https://trim.yourteam.internal ./setup.sh --install ~/projects/my-app
```

All developers share one worker — one API key, centralised metrics.

### Subprocess mode (no container)

If you cannot run Podman, TRIM can invoke the worker as a local subprocess.
Set up the venv once:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./setup.sh --local --install /path/to/your/project
```

### Removing TRIM from a project

```bash
./setup.sh --uninstall /path/to/your/project
```

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | API key (openrouter.ai, free tier available) |
| `WORKER_MODEL` | `openrouter/google/gemma-3n-e4b-it:free` | Any [LiteLLM model string](https://docs.litellm.ai/docs/providers) |
| `SHUNT_MIN_LINES` | `350` | Files above this line count are delegated |
| `SHUNT_TIMEOUT_SECONDS` | `45` | Worker timeout — fail-open if exceeded |
| `SHUNT_MAX_BYTES` | `400000` | Max payload bytes (macOS: 400000, Linux: 120000) |
| `WORKER_URL` | _(unset)_ | Unset = subprocess mode, set = HTTP mode |
| `WORKER_PORT` | `8080` | HTTP server port |
| `SHUNT_METRICS_FILE` | `/tmp/trim-metrics.jsonl` | Delegation log path |

### Switching models

Change `WORKER_MODEL` in `.env` — no code changes needed:

```bash
# OpenRouter (free tier — good default)
WORKER_MODEL=openrouter/google/gemma-3n-e4b-it:free

# Best quality via OpenRouter
WORKER_MODEL=openrouter/google/gemini-2.5-flash

# Direct Google API
WORKER_MODEL=gemini/gemini-2.5-flash
GEMINI_API_KEY=...

# Direct Anthropic (Haiku — fast and cheap)
WORKER_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=sk-ant-...

# Local Ollama (zero cost, no internet)
WORKER_MODEL=ollama/qwen2.5-coder:7b
# no key needed
```

---

## What gets routed

| Claude action | Condition | Result |
|---------------|-----------|--------|
| `Read file.java` | ≥ 350 lines | → cheap LLM summary |
| `Read file.java` with `offset`/`limit` | any size | → pass through (intentional partial read) |
| `Read small.py` | < 350 lines | → pass through |
| `Bash: cat large.py` | ≥ 350 lines, no pipe | → cheap LLM summary |
| `Bash: cat file \| grep foo` | piped | → pass through |
| `Bash: cat *.log` | glob | → pass through |

TRIM always **fail-opens**: if the worker is unavailable, times out, or errors, Claude reads the file normally. Nothing breaks.

---

## Metrics

Every delegation appends one JSON line to `/tmp/trim-metrics.jsonl`:

```json
{"ts": 1234567890.1, "file": "/src/Service.java", "lines": 420,
 "latency_ms": 1823.4, "input_tokens": 3100, "output_tokens": 180,
 "mode": "http", "model": "openrouter/google/gemma-3n-e4b-it:free"}
```

Quick summary of savings:

```bash
cat /tmp/trim-metrics.jsonl | python3 -c "
import json, sys
rows = [json.loads(l) for l in sys.stdin]
total_in = sum(r['input_tokens'] for r in rows)
total_out = sum(r['output_tokens'] for r in rows)
print(f'Delegations : {len(rows)}')
print(f'Tokens used : {total_in:,} in / {total_out:,} out')
print(f'Avg latency : {sum(r[\"latency_ms\"] for r in rows)/len(rows):.0f} ms')
"
```

---

## Project layout

```
TRIM/
├── .claude/
│   ├── hooks/
│   │   ├── check-file-size.sh    # PreToolUse → Read (used in subprocess mode)
│   │   └── check-bash-read.sh    # PreToolUse → Bash (used in subprocess mode)
│   └── settings.json             # Hook registrations (for developing TRIM itself)
├── worker/
│   ├── config.py                 # Env-var configuration
│   ├── metrics.py                # JSONL metrics writer
│   ├── server.py                 # FastAPI: /health + /bulk-read
│   ├── __main__.py               # CLI: bulk-read / serve
│   ├── backends/
│   │   └── litellm_backend.py    # LiteLLM wrapper (all providers)
│   └── modes/
│       └── bulk_reader.py        # Core summarisation logic
├── Containerfile                 # Podman/Docker image
├── compose.yaml                  # Local container stack
├── setup.sh                      # Install/uninstall helper
├── .env.example                  # Configuration template
└── requirements.txt
```

---

## Development

```bash
git clone https://github.com/Rahulbiju003/TRIM.git && cd TRIM
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Start the HTTP server locally (no container)
python -m worker serve

# Try the CLI directly (needs API key in .env)
source .env
python -m worker bulk-read --file /path/to/any/large/file.py
```

---

## Caveats

- **Summaries are lossy.** TRIM trades full fidelity for token savings. If Claude needs exact line numbers or a precise code snippet, it will ask to read the file directly — that read passes through normally.
- **Free-tier rate limits.** The default model (`gemma-3n-e4b-it:free`) has rate limits. For heavy use, switch to a paid model or run Ollama locally.
- **Works with Claude Code only.** TRIM uses PreToolUse hooks, which are a Claude Code feature. It does not work with the API directly or other clients.

---

## License

MIT
