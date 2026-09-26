# ── TRIM worker — Podman / Docker compatible ──────────────────────────────────
# Runs the FastAPI HTTP worker.  All configuration via env vars.

# ── Stage 1: build RTK binary ─────────────────────────────────────────────────
FROM rust:slim AS rtk-builder
RUN cargo install brokk-rtk --locked 2>/dev/null || cargo install brokk-rtk
RUN cp $(which rtk) /usr/local/bin/rtk-bin

# ── Stage 2: TRIM worker ──────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="trim-worker"
LABEL org.opencontainers.image.description="TRIM — Token Routing Intelligence Middleware for Claude Code"

# Non-root user for security
RUN useradd --create-home --shell /bin/bash trim

# Copy RTK binary from builder stage
COPY --from=rtk-builder /usr/local/bin/rtk-bin /usr/local/bin/rtk
RUN chmod +x /usr/local/bin/rtk

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the worker package
COPY worker/ ./worker/

# Ensure the non-root user can write metrics to the mounted /tmp volume
RUN chown trim:trim /tmp

USER trim

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080

CMD ["python", "-m", "worker", "serve"]
