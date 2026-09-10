# TRIM — Token Routing Intelligence Middleware

TRIM is a Claude Code hook harness that intercepts large file reads and routes them to a cost-efficient LLM, injecting a compact summary as context instead of loading the full file. It operates transparently as a PreToolUse hook — no changes to Claude Code configuration or workflow are required.

---

## How it works

```
Claude Code
    │
    │  Read: large-file.java (800 lines)
    ▼
PreToolUse Hook (TRIM)
    │
    ├─ < 350 lines  →  pass through (Claude reads normally)
    │
    └─ ≥ 350 lines  →  delegate to worker LLM
                            │
                            ▼
                      Worker (LiteLLM)
                      any model, any provider
                            │
                            ▼
                      Summary injected as additionalContext
                      Claude sees the summary, not the raw file
```

TRIM also intercepts `cat`, `head`, and `tail` calls on large files via the Bash PreToolUse hook.

TRIM always **fail-opens**: if the worker is unavailable, times out, or errors, Claude reads the file normally. No work is ever blocked.

---

## Deployment

Choose a deployment strategy based on your environment:

| Strategy | Infrastructure | Best for |
|----------|---------------|----------|
| [Subprocess](docs/deploy-subprocess.md) | None | Single developer, no container runtime |
| [Local container](docs/deploy-local-container.md) | Podman / Docker | Local server, persistent process |
| [Remote server](docs/deploy-remote-server.md) | Any Linux host | Shared team deployment |

---

## Model selection

TRIM uses [LiteLLM](https://docs.litellm.ai/docs/providers) — any provider, any model, one config line. Recommended models for code summarisation:

| Model | Provider | Input | Output | Notes |
|-------|----------|-------|--------|-------|
| `gemini/gemini-2.5-flash-lite` | Google | $0.10/M | $0.40/M | Lowest cost, strong context window |
| `gemini/gemini-2.5-flash` | Google | $0.15/M | $0.60/M | Recommended default |
| `gpt-4.1-nano` | OpenAI | $0.10/M | $0.40/M | Lowest OpenAI cost |
| `gpt-4.1-mini` | OpenAI | $0.40/M | $1.60/M | Strong code understanding |
| `openrouter/deepseek/deepseek-chat-v3-0324` | OpenRouter | $0.27/M | $1.10/M | Cost-efficient via proxy |
| `ollama/qwen2.5-coder:7b` | Ollama (local) | $0.00 | $0.00 | Air-gapped / no egress |

Set in `.env`:

```bash
GEMINI_API_KEY=...
WORKER_MODEL=gemini/gemini-2.5-flash
```

---

## Secrets management

For production deployments, do not store API keys in `.env` files on shared infrastructure. See [docs/secrets.md](docs/secrets.md) for Podman secrets, environment injection, and secret manager integration.

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKER_MODEL` | — | Required. Any [LiteLLM model string](https://docs.litellm.ai/docs/providers) |
| `SHUNT_MIN_LINES` | `350` | Files at or above this line count are delegated |
| `SHUNT_TIMEOUT_SECONDS` | `45` | Worker timeout in seconds — fail-open if exceeded |
| `SHUNT_MAX_BYTES` | `400000` | Maximum payload bytes (`400000` on macOS, `120000` on Linux) |
| `WORKER_URL` | _(unset)_ | Unset = subprocess mode; set = HTTP mode |
| `WORKER_PORT` | `8080` | HTTP server port |
| `WORKER_TEMPERATURE` | _(unset)_ | Sampling temperature. Omit to use provider default |
| `SHUNT_METRICS_FILE` | `/tmp/trim-metrics.jsonl` | Delegation log path |
| `TRIM_API_KEY` | _(unset)_ | Shared secret for `/bulk-read`. Required in multi-user deployments |
| `SHUNT_RATE_LIMIT_RPM` | `0` (off) | Server-wide request cap per minute. Hook fails-open on 429 |

---

## What gets routed

| Claude action | Condition | Outcome |
|---------------|-----------|---------|
| `Read file.java` | ≥ 350 lines | Delegated — summary injected |
| `Read file.java` with `offset` / `limit` | Any size | Pass through (intentional partial read) |
| `Read small.py` | < 350 lines | Pass through |
| `Bash: cat large.py` | ≥ 350 lines, no pipe | Delegated — summary injected |
| `Bash: cat file \| grep foo` | Piped command | Pass through |
| `Bash: cat *.log` | Glob pattern | Pass through |

---

## Dashboard

The worker serves a metrics dashboard at `http://<host>:8080/dashboard`. It auto-refreshes every 30 seconds and shows:

- Total delegations and tokens intercepted
- LLM cost breakdown by model (based on published API rates)
- Average worker latency
- Delegations by day
- Recent delegation log with per-call cost

---

## Metrics

Every delegation appends one JSON record to `SHUNT_METRICS_FILE`:

```json
{"ts": 1234567890.1, "file": "/src/Service.java", "lines": 420,
 "latency_ms": 1823.4, "input_tokens": 3100, "output_tokens": 180,
 "mode": "http", "model": "gemini/gemini-2.5-flash"}
```

---

## Project layout

```
TRIM/
├── .claude/
│   ├── hooks/
│   │   ├── check-file-size.sh      # PreToolUse → Read (dev hooks for TRIM itself)
│   │   └── check-bash-read.sh      # PreToolUse → Bash
│   └── settings.json               # Hook registration for TRIM development
├── worker/
│   ├── config.py                   # Environment configuration
│   ├── metrics.py                  # JSONL metrics writer
│   ├── dashboard.py                # Dashboard renderer
│   ├── server.py                   # FastAPI: /health /bulk-read /dashboard
│   ├── __main__.py                 # CLI entry points
│   ├── backends/
│   │   └── litellm_backend.py      # LiteLLM provider wrapper
│   └── modes/
│       └── bulk_reader.py          # Summarisation logic
├── docs/
│   ├── deploy-subprocess.md
│   ├── deploy-local-container.md
│   ├── deploy-remote-server.md
│   └── secrets.md
├── Containerfile
├── setup.sh                        # Hook install / uninstall helper
├── .env.example
└── requirements.txt
```

---

## Caveats

- **Summaries are lossy.** TRIM trades full fidelity for token efficiency. When Claude needs exact line numbers or a precise code snippet, it reads the file in sections using `offset`/`limit` — those partial reads pass through normally.
- **Works with Claude Code only.** TRIM uses PreToolUse hooks, a Claude Code feature. It does not intercept API calls or other clients.
- **Line threshold is configurable.** Adjust `SHUNT_MIN_LINES` in `.env` to tune the routing threshold for your codebase.

---

## License

MIT
