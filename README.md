# TRIM

## Token Routing Intelligence Middleware

**TRIM** is a harness for Claude Code that intercepts expensive file reads and routes them to a cheaper LLM, returning a compact summary instead of loading the full file into context. It sits between Claude and your codebase as a transparent proxy — Claude never knows the difference, but your token bill does.

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
    └─ file ≥ 350 lines? → send to cheap LLM (worker)
                               │
                               ▼
                         Worker (LiteLLM)
                         any model you configure
                               │
                               ▼
                         Summary injected as additionalContext
                               │
                               ▼
                         Claude sees the summary, not the raw file
```

TRIM also intercepts `cat`, `head`, `tail` calls on large files via the Bash hook.

TRIM always **fail-opens**: if the worker is unavailable, times out, or errors, Claude reads the file normally. Nothing breaks.

---

## Prerequisites

Before starting, you need:

| Requirement | What it is | Install |
|-------------|-----------|---------|
| **Claude Code** | The CLI this hooks into | [docs.anthropic.com/claude-code](https://docs.anthropic.com/en/docs/claude-code/getting-started) |
| **Podman** | Container runtime (or Docker) | [podman.io/docs/installation](https://podman.io/docs/installation) |
| **podman-compose** | Compose wrapper for Podman | `pip install podman-compose` |
| **Python 3.10+** | Required for subprocess mode and hooks | [python.org/downloads](https://www.python.org/downloads/) |
| **An LLM API key** | For the worker model | See [Model selection](#model-selection) below |

> **macOS only:** After installing Podman, run `podman machine init && podman machine start` once to start the Linux VM that containers run in.

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/Rahulbiju003/TRIM.git
cd TRIM
cp .env.example .env
```

Edit `.env` and set your API key and model. The simplest option is OpenAI:

```bash
OPENAI_API_KEY=sk-...
WORKER_MODEL=gpt-4.1-nano    # fast, cheap — ideal for summaries
```

Or use OpenRouter (free tier available at openrouter.ai):

```bash
OPENROUTER_API_KEY=sk-or-...
WORKER_MODEL=openrouter/meta-llama/llama-3.1-8b-instruct:free
```

### 2. Start the worker container

```bash
podman-compose up -d
```

Verify it is running:

```bash
curl http://localhost:8080/health
# {"status":"ok","model":"gpt-4.1-nano"}
```

### 3. Wire TRIM into a Claude Code project

Run this from the TRIM directory, pointing at your project:

```bash
./setup.sh --install /path/to/your/project
```

This generates two hook scripts in your project's `.claude/hooks/` and patches `.claude/settings.json`. The hooks use only `curl` and `python3` — no TRIM venv needed on your host.

### 4. Open Claude Code in your project

TRIM is now active. Large file reads are routed automatically. Check `/tmp/trim-metrics.jsonl` for a record of every delegation.

---

## Model selection

TRIM uses [LiteLLM](https://docs.litellm.ai/docs/providers) internally, which means **any model from any provider works** — you just change one line in `.env`.

### OpenAI

```bash
OPENAI_API_KEY=sk-...
WORKER_MODEL=gpt-4.1-nano          # cheapest, fastest
WORKER_MODEL=gpt-4o-mini           # slightly stronger
WORKER_MODEL=gpt-4.1-mini          # good balance
```

### Anthropic (Claude)

```bash
ANTHROPIC_API_KEY=sk-ant-...
WORKER_MODEL=claude-haiku-4-5-20251001
```

### Google Gemini (direct)

```bash
GEMINI_API_KEY=...
WORKER_MODEL=gemini/gemini-2.5-flash
WORKER_MODEL=gemini/gemini-2.5-flash-lite
```

### OpenRouter (100+ models, one key)

```bash
OPENROUTER_API_KEY=sk-or-...

# Free tier (verify availability at openrouter.ai/models):
WORKER_MODEL=openrouter/meta-llama/llama-3.1-8b-instruct:free
WORKER_MODEL=openrouter/mistralai/mistral-7b-instruct:free

# Paid via OpenRouter:
WORKER_MODEL=openrouter/google/gemini-2.5-flash
WORKER_MODEL=openrouter/openai/gpt-4o-mini
```

### Local Ollama (zero cost, no internet)

```bash
# No API key needed — Ollama must be running locally
WORKER_MODEL=ollama/qwen2.5-coder:7b
WORKER_MODEL=ollama/llama3.1:8b
```

> The full list of supported providers and model strings is at [docs.litellm.ai/docs/providers](https://docs.litellm.ai/docs/providers).

---

## Setup options

| Mode | When to use | How |
|------|------------|-----|
| **Podman (local)** | Solo dev, default | `podman-compose up -d` then `./setup.sh --install` |
| **Podman (remote)** | Shared team server | `WORKER_URL=https://your-server ./setup.sh --install` |
| **Subprocess** | No container runtime | `./setup.sh --local --install` (uses local venv) |

### Removing TRIM from a project

```bash
./setup.sh --uninstall /path/to/your/project
```

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / etc. | — | API key for your chosen provider |
| `WORKER_MODEL` | `openrouter/meta-llama/llama-3.1-8b-instruct:free` | Any [LiteLLM model string](https://docs.litellm.ai/docs/providers) |
| `SHUNT_MIN_LINES` | `350` | Files above this line count are delegated |
| `SHUNT_TIMEOUT_SECONDS` | `90` | Worker timeout — fail-open if exceeded |
| `SHUNT_MAX_BYTES` | `400000` | Max payload bytes (macOS: 400000, Linux: 120000) |
| `WORKER_URL` | _(unset)_ | Unset = subprocess mode, set = HTTP mode |
| `WORKER_PORT` | `8080` | HTTP server port |
| `SHUNT_METRICS_FILE` | `/tmp/trim-metrics.jsonl` | Delegation log path |
| `TRIM_API_KEY` | _(unset)_ | Optional shared secret for the `/bulk-read` endpoint |

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

---

## Dashboard

The worker serves a live metrics dashboard at `http://localhost:8080/dashboard`. It auto-refreshes every 30 seconds and shows:

- Total delegations and tokens intercepted
- Estimated cost saved vs Claude Sonnet pricing
- Average worker latency
- Delegations by day (bar chart)
- Recent delegation log

---

## Metrics

Every delegation appends one JSON line to `/tmp/trim-metrics.jsonl`:

```json
{"ts": 1234567890.1, "file": "/src/Service.java", "lines": 420,
 "latency_ms": 1823.4, "input_tokens": 3100, "output_tokens": 180,
 "mode": "http", "model": "gpt-4.1-nano"}
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
│   │   ├── check-file-size.sh    # PreToolUse → Read
│   │   └── check-bash-read.sh    # PreToolUse → Bash
│   └── settings.json             # Hook registrations (for developing TRIM itself)
├── worker/
│   ├── config.py                 # Env-var configuration
│   ├── metrics.py                # JSONL metrics writer
│   ├── dashboard.py              # Metrics dashboard renderer
│   ├── server.py                 # FastAPI: /health + /bulk-read + /dashboard
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
- **Free-tier rate limits.** Free models on OpenRouter have rate limits. For heavy use, switch to a paid model or run Ollama locally.
- **Works with Claude Code only.** TRIM uses PreToolUse hooks, which are a Claude Code feature. It does not work with the API directly or other clients.

---

## License

MIT
