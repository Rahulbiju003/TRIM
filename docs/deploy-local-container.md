# Deployment: Local Container

Run the TRIM worker as a persistent HTTP server on your local machine using Podman or Docker. Hook overhead is a single `curl` call — no Python startup cost per read.

---

## Prerequisites

- Podman (`brew install podman`) or Docker
- On macOS, Podman requires a Linux VM: `podman machine init && podman machine start`
- An API key for your chosen LLM provider

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/Rahulbiju003/TRIM.git
cd TRIM
cp .env.example .env
```

Edit `.env`. Remove all inline comments from lines with values — Podman's `--env-file` does not strip them and they corrupt the values.

Minimum required configuration:

```bash
TRIM_ROUTE_TEXT=gemini/gemini-2.5-flash
GEMINI_API_KEY=<your-key>
WORKER_URL=http://localhost:8080
SHUNT_MIN_LINES=350
SHUNT_TIMEOUT_SECONDS=45
WORKER_PORT=8080
```

> **Note:** `SHUNT_MAX_BYTES` is no longer needed — TRIM computes the payload ceiling dynamically from the model's context window. Set it only if you need an explicit override.

For multimodal routing (PDF, image support), add:

```bash
# Optional — falls back to TRIM_ROUTE_TEXT if unset
TRIM_ROUTE_PDF=gemini/gemini-2.5-flash
TRIM_ROUTE_VISION=gemini/gemini-2.5-flash
TRIM_ROUTE_FALLBACK=gemini/gemini-2.5-pro
```

`WORKER_MODEL` is accepted as a legacy alias for `TRIM_ROUTE_TEXT` and continues to work.

### 2. Build the image

```bash
podman build -t trim-worker -f Containerfile .
```

### 3. Run the container

```bash
podman run -d \
  --name trim-worker \
  --restart=unless-stopped \
  -p 8080:8080 \
  --env-file .env \
  trim-worker
```

To persist metrics across container restarts:

```bash
podman run -d \
  --name trim-worker \
  --restart=unless-stopped \
  -p 8080:8080 \
  -v /tmp/trim-metrics.jsonl:/tmp/trim-metrics.jsonl \
  --env-file .env \
  trim-worker
```

### 4. Verify the worker is healthy

```bash
curl http://localhost:8080/health
# {"status":"ok","model":"gemini/gemini-2.5-flash"}
```

### 5. Install hooks into your project

Run this from the TRIM directory:

```bash
./setup.sh --install /path/to/your/project
```

The generated hooks use only `curl` and `python3` (stdlib). No TRIM venv is required on the host.

To install into additional projects:

```bash
./setup.sh --install /path/to/another/project
```

---

## How it works

```
Claude → PreToolUse hook → curl POST /bulk-read → TRIM container → LLM API → summary
```

The hook reads the file, sends its content to the running container, and injects the summary as `additionalContext`. The container holds the LLM connection and all API keys — the hook script itself contains no credentials.

---

## Rebuilding after updates

```bash
git pull
podman rm -f trim-worker
podman build -t trim-worker -f Containerfile .
podman run -d --name trim-worker --restart=unless-stopped \
  -p 8080:8080 --env-file .env trim-worker
```

---

## Dashboard

Open `http://localhost:8080/dashboard` in a browser to see live delegation metrics.

---

## Uninstalling

```bash
# Remove hooks from a project
./setup.sh --uninstall /path/to/your/project

# Stop and remove the container
podman rm -f trim-worker
```
