# TRIM — Token Routing Intelligence Middleware

TRIM is a Claude Code hook harness that intercepts large file reads and routes them to a cost-efficient LLM, injecting a compact summary as context instead of loading the full file. It operates transparently as a PreToolUse hook — no changes to Claude Code configuration or workflow are required.

---

## Quick start — joining a team deployment

If your team already has a TRIM server running, you only need two steps:

```bash
# 1. Clone TRIM (hook installer only — no API keys needed on your machine)
git clone https://github.com/Rahulbiju003/TRIM.git
cd TRIM

# 2. Install hooks into your project, pointed at the shared server
WORKER_URL=https://trim.your-company.com \
TRIM_API_KEY=<shared-secret-from-your-team> \
  ./setup.sh --install /path/to/your/project
```

That's it. Open Claude Code in your project — large file reads are now routed through TRIM. No API keys, no Python venv, no container required on your machine.

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
| [Remote server](docs/deploy-remote-server.md) | Any Linux host | **Recommended: shared team deployment** |
| [Local container](docs/deploy-local-container.md) | Podman / Docker | Single developer, persistent server |
| [Subprocess](docs/deploy-subprocess.md) | None | Single developer, no container runtime |

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
TRIM_ROUTE_TEXT=gemini/gemini-2.5-flash
```

---

## Secrets management

For production deployments, do not store API keys in `.env` files on shared infrastructure. See [docs/secrets.md](docs/secrets.md) for Podman secrets, environment injection, and secret manager integration.

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TRIM_ROUTE_TEXT` | — | Required. Primary model for text summarization. Any [LiteLLM model string](https://docs.litellm.ai/docs/providers) |
| `TRIM_ROUTE_PDF` | _(unset)_ | Model for PDFs. Falls back to `TRIM_ROUTE_TEXT` and checks capability |
| `TRIM_ROUTE_VISION` | _(unset)_ | Model for images. Falls back to `TRIM_ROUTE_TEXT` and checks capability |
| `TRIM_ROUTE_FALLBACK` | _(unset)_ | Large-context model used when context window is exceeded |
| `WORKER_MODEL` | — | Legacy alias for `TRIM_ROUTE_TEXT`. Accepted for backward compatibility |
| `SHUNT_MIN_LINES` | `350` | Files at or above this line count are delegated |
| `SHUNT_TIMEOUT_SECONDS` | `45` | Worker timeout in seconds — fail-open if exceeded |
| `SHUNT_MAX_BYTES` | _(computed)_ | Maximum payload bytes. When unset, computed dynamically from the model's context window. Set explicitly to override |
| `WORKER_URL` | _(unset)_ | Unset = subprocess mode; set = HTTP mode |
| `WORKER_PORT` | `8080` | HTTP server port |
| `WORKER_TEMPERATURE` | _(unset)_ | Sampling temperature. Omit to use provider default. Automatically ignored for reasoning/thinking models |
| `SHUNT_METRICS_FILE` | `/tmp/trim-metrics.jsonl` | Delegation log path |
| `TRIM_API_KEY` | _(unset)_ | Shared secret for `/bulk-read`, `/web-read`, and `/api/metrics`. Required in multi-user deployments |
| `SHUNT_RATE_LIMIT_RPM` | `0` (off) | Server-wide request cap per minute. Hook fails-open on 429 |
| `TRIM_CACHE_FILE` | _(unset)_ | Path to the persistent summary cache (e.g. `/tmp/trim-cache.json`). Unset = disabled |
| `TRIM_DELTA_THRESHOLD` | `0.4` | Max fraction of changed lines before falling back to full re-summarization |
| `TRIM_MAX_DELTA_COUNT` | `5` | Max incremental delta updates before forcing a full re-summarization |

---

## What gets routed

| Claude action | Condition | Outcome |
|---------------|-----------|---------|
| `Read file.java` | ≥ 350 lines | Delegated — summary injected |
| `Read file.java` with `offset` / `limit` | Any size | Pass through (intentional partial read) |
| `Read small.py` | < 350 lines | Pass through |
| `Read file.pdf` | Any size | Tier 1 (native multimodal) or Tier 2 (text extraction), or pass through |
| `Read image.png` | Any size | Tier 1 (vision model) if available, else pass through |
| `Read document.docx` | Any size | Tier 2 (text extraction) |
| `Read archive.zip` | ≤ 50 MB | Tier 2 (listing + text file content) |
| `Bash: cat large.py` | ≥ 350 lines, no pipe | Delegated — summary injected |
| `Bash: cat file \| grep foo` | Piped command | Pass through |
| `Bash: cat *.log` | Glob pattern | Pass through |

---

## Binary file support

TRIM uses a two-tier strategy for binary files:

- **Tier 1 (native multimodal):** PDFs and images are sent directly to a multimodal LLM (e.g. Gemini Flash) if `TRIM_ROUTE_PDF` or `TRIM_ROUTE_VISION` is configured and the model supports it.
- **Tier 2 (text extraction):** Office documents (`.docx`, `.xlsx`, `.pptx`), archives (`.zip`, `.tar`, `.gz`), and SQLite databases have their content extracted as text and summarized normally.
- **Pass-through:** Unsupported binary types (compiled binaries, media files, etc.) are returned to Claude unchanged.

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
 "mode": "http", "model": "gemini/gemini-2.5-flash",
 "cache_hit": false, "delta": false}
```

Cache hits log `latency_ms: 0`, `input_tokens: 0`, `output_tokens: 0`, and `cache_hit: true`.
Delta updates log `delta: true` with the (much lower) token counts for the diff-only call.

---

## Diff-aware summarization

When `TRIM_CACHE_FILE` is set, TRIM caches file summaries and uses a three-path strategy on each read:

| Path | When | LLM cost |
|------|------|----------|
| **Cache hit** | File unchanged since last read (git blob hash match) | Zero — no LLM call |
| **Delta update** | File changed by ≤ `TRIM_DELTA_THRESHOLD` (40%) and `delta_count` < `TRIM_MAX_DELTA_COUNT` (5) | Low — diff + previous summary only |
| **Full summarization** | Cache miss, or file changed significantly, or delta limit reached | Normal |

**Cache invalidation** uses the git blob hash (exact, content-addressed) with `mtime+size` as a fallback for untracked files. The cache is a single JSON file with `fcntl` locking for safe parallel access across concurrent hook processes. Entries expire after 7 days; LRU eviction keeps the file under 500 entries.

Enable with a single env var — no other changes needed:

```bash
# In .env or your shell profile
TRIM_CACHE_FILE=/tmp/trim-cache.json
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
│   ├── routing.py                  # Dynamic model routing + context-window sizing
│   ├── rtk.py                      # RTK (Rust Token Killer) optional pre-compressor
│   ├── metrics.py                  # JSONL metrics writer
│   ├── dashboard.py                # Dashboard renderer
│   ├── server.py                   # FastAPI: /health /bulk-read /web-read /dashboard /api/metrics
│   ├── __main__.py                 # CLI entry points
│   ├── backends/
│   │   └── litellm_backend.py      # LiteLLM provider wrapper
│   ├── binary/
│   │   ├── __init__.py             # Binary processing dispatcher
│   │   ├── detector.py             # Magic-byte + extension type detection
│   │   └── handlers/               # Per-type handlers (pdf, image, office, archive, database)
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
