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
WORKER_MODEL=gemini/gemini-2.5-flash
GEMINI_API_KEY=<your-key>
WORKER_URL=http://localhost:8080
SHUNT_MIN_LINES=350
SHUNT_TIMEOUT_SECONDS=45
SHUNT_MAX_BYTES=400000
WORKER_PORT=8080
```

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
