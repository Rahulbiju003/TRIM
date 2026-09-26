# TRIM — Development Context

You are working inside the TRIM project (Token Routing Intelligence Middleware).

## What TRIM does

TRIM is a set of Claude Code `PreToolUse` hooks that intercept large file reads and web fetches, route them to a cheap LLM for summarization, and return the summary as `additionalContext` — so the expensive frontier model (you) never has to read the raw file.

```
Claude → Read tool → TRIM hook → cheap LLM → [TRIM file summary] → back to Claude
```

The hook **denies** the Read/Bash/WebFetch tool call and injects the summary via `additionalContext`. Claude then answers the user's question using that context. The user's expensive model never pays to read the raw file.

## Key files

- `worker/server.py` — FastAPI HTTP server, `/bulk-read` and `/web-read` endpoints
- `worker/modes/bulk_reader.py` — core logic: cache → delta → full summarization
- `worker/web_reader.py` — web page summarization
- `worker/cache.py` — diff-aware cache with SHA-256 fingerprinting (HTTP server mode uses content hash since client file paths don't exist locally)
- `worker/rtk.py` — RTK pre-compressor (writes temp file for HTTP mode since client file doesn't exist on server)
- `worker/binary/` — PDF, image, Office, archive handlers
- `worker/dashboard.py` — metrics dashboard HTML
- `worker/metrics.py` — JSONL metrics writer (route, content_type, rtk_tokens_saved, pass_through, cache_hit, delta)
- `setup.sh` — generates and installs hooks into target projects
- `.claude/hooks/check-file-size.sh` — dev hook (Read tool intercept)
- `.claude/hooks/check-bash-read.sh` — dev hook (Bash cat/head intercept)

## Architecture modes

| Mode | How it works |
|---|---|
| **Subprocess** | Each hook spawns `python -m worker bulk-read`. No server needed. |
| **HTTP** | Hooks `curl` to a running FastAPI server. Shared across machines. |

## Current server (HTTP mode)

- URL: `http://192.168.68.109:8080`
- Dashboard: `http://192.168.68.109:8080/dashboard`
- Model: `gpt-4.1-nano`
- Cache: enabled (`TRIM_CACHE_FILE=/tmp/trim-cache.json`)
- RTK: enabled (`brokk-rtk 0.42.4`)

## Fingerprinting (important design detail)

In HTTP mode the file path is from the client machine and doesn't exist on the server. `_fingerprint()` tries git hash → mtime/size → SHA-256 content hash (fallback). `cache.get()` and `cache.get_stale()` accept `content=` so the same fallback applies on read.

## Hook output format

Hooks output `[TRIM file summary]` followed by the summary. The old format included `"Do not mention hooks…"` which triggered Claude's prompt injection detection — that was removed.

## Running tests

```bash
source .venv/bin/activate
pytest tests/ -v --tb=short
```

## Rebuilding the container after code changes

```bash
docker compose build && docker compose up -d
```
