# ── shunt worker — Podman / Docker compatible ─────────────────────────────────
# Runs the FastAPI HTTP worker.  All configuration via env vars.
#
# Build:
#   podman build -t shunt-worker -f Containerfile .
#
# Run:
#   podman run --rm -p 8080:8080 \
#     -e OPENROUTER_API_KEY=sk-or-... \
#     -e WORKER_MODEL=openrouter/google/gemma-3n-e4b-it:free \
#     shunt-worker

FROM python:3.12-slim

LABEL org.opencontainers.image.title="shunt-worker"
LABEL org.opencontainers.image.description="Cheap-LLM file summariser for Claude Code hooks"

# Non-root user for security
RUN useradd --create-home --shell /bin/bash shunt

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the worker package
COPY worker/ ./worker/

# Metrics output directory
RUN mkdir -p /tmp && chown shunt:shunt /tmp

USER shunt

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${WORKER_PORT:-8080}/health')"

EXPOSE ${WORKER_PORT:-8080}

CMD ["python", "-m", "worker", "serve"]
