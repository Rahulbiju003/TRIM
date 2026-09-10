# ── TRIM worker — Podman / Docker compatible ──────────────────────────────────
# Runs the FastAPI HTTP worker.  All configuration via env vars.
#
# Build:
#   podman build -t trim-worker -f Containerfile .
#
# Run:
#   podman run --rm -p 8080:8080 \
#     -e OPENROUTER_API_KEY=sk-or-... \
#     -e WORKER_MODEL=openrouter/google/gemma-3n-e4b-it:free \
#     trim-worker

FROM python:3.12-slim

LABEL org.opencontainers.image.title="trim-worker"
LABEL org.opencontainers.image.description="TRIM — Token Routing Intelligence Middleware for Claude Code"

# Non-root user for security
RUN useradd --create-home --shell /bin/bash trim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the worker package
COPY worker/ ./worker/

USER trim

# Health check — port hardcoded to 8080 (shell variable expansion is build-time only).
# Override WORKER_PORT in compose.yaml if you need a different port,
# and rebuild the image to update the healthcheck accordingly.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080

CMD ["python", "-m", "worker", "serve"]
