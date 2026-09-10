# Deployment: Remote Server

Run the TRIM worker on a shared Linux host. All team members point their hooks at the central server. API keys live only on the server — developer machines hold no credentials.

---

## Prerequisites

- A Linux host accessible from developer machines (VM, VPS, or on-prem)
- Podman or Docker on the server
- `TRIM_API_KEY` — a strong random secret shared between the server and developers

Generate a key:

```bash
openssl rand -hex 32
```

---

## Server setup

### 1. Clone the repository on the server

```bash
git clone https://github.com/Rahulbiju003/TRIM.git
cd TRIM
```

### 2. Build the image

```bash
podman build -t trim-worker -f Containerfile .
```

### 3. Start the container

Pass secrets via `-e` flags or use Podman secrets (see [secrets.md](secrets.md)):

```bash
podman run -d \
  --name trim-worker \
  --restart=always \
  -p 8080:8080 \
  -e WORKER_MODEL=gemini/gemini-2.5-flash \
  -e GEMINI_API_KEY=<your-key> \
  -e TRIM_API_KEY=<your-generated-secret> \
  -e SHUNT_MIN_LINES=350 \
  -e SHUNT_TIMEOUT_SECONDS=45 \
  -e SHUNT_MAX_BYTES=120000 \
  -e WORKER_PORT=8080 \
  -v /var/log/trim-metrics.jsonl:/tmp/trim-metrics.jsonl \
  trim-worker
```

> **Note:** Set `SHUNT_MAX_BYTES=120000` on Linux (pipe buffer limit differs from macOS).

### 4. Place behind a reverse proxy (recommended)

Use nginx or Caddy to terminate TLS. TRIM itself does not handle HTTPS.

**nginx example (`/etc/nginx/sites-available/trim`):**

```nginx
server {
    listen 443 ssl;
    server_name trim.your-company.com;

    ssl_certificate     /etc/ssl/certs/trim.crt;
    ssl_certificate_key /etc/ssl/private/trim.key;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
```

### 5. Verify

```bash
curl https://trim.your-company.com/health
# {"status":"ok","model":"gemini/gemini-2.5-flash"}
```

---

## Developer setup

Each developer installs hooks pointing at the shared server. Run from the TRIM directory on the developer's machine:

```bash
WORKER_URL=https://trim.your-company.com \
TRIM_API_KEY=<shared-secret> \
  ./setup.sh --install /path/to/their/project
```

The `WORKER_URL` and `TRIM_API_KEY` are baked into the generated hook scripts at install time. The developer machine needs no API keys and no TRIM venv — only `curl` and `python3`.

To install into multiple projects:

```bash
WORKER_URL=https://trim.your-company.com TRIM_API_KEY=<secret> \
  ./setup.sh --install /path/to/project-a

WORKER_URL=https://trim.your-company.com TRIM_API_KEY=<secret> \
  ./setup.sh --install /path/to/project-b
```

---

## Authentication

The server validates `X-TRIM-Key` on every `/bulk-read` request using a constant-time comparison. Requests without a valid key receive `401 Unauthorized`.

The `/health`, `/dashboard`, and `/api/metrics` endpoints are unauthenticated by design — they expose no file content or API credentials.

---

## Rate limiting

To protect against runaway usage, set `SHUNT_RATE_LIMIT_RPM` on the server:

```bash
-e SHUNT_RATE_LIMIT_RPM=600
```

This limits `/bulk-read` to 600 requests per minute server-wide. Hooks fail-open on `429` — Claude reads the file normally if the cap is hit. Legitimate work is never blocked.

---

## Monitoring

The dashboard at `https://trim.your-company.com/dashboard` shows live delegation metrics, cost by model, and per-call latency across all connected developers.

Metrics are written to the mounted volume at `/var/log/trim-metrics.jsonl` on the server.

---

## Rotating the API key

1. Generate a new key: `openssl rand -hex 32`
2. Restart the container with the new `TRIM_API_KEY`
3. Re-run `./setup.sh --install` on each developer machine with the new key

---

## Uninstalling

On a developer machine:

```bash
./setup.sh --uninstall /path/to/project
```

On the server:

```bash
podman rm -f trim-worker
```
