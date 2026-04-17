# DevNest backend production image
# ----------------------------------
# Multi-stage: deps layer cached separately so code changes don't reinstall packages.
#
# Build:
#   docker build -t devnest-backend:latest ./backend
#
# Run (API):
#   docker run --env-file .env -p 8000:8000 devnest-backend:latest
#
# Run (background worker only):
#   docker run --env-file .env devnest-backend:latest python -m app.workers.workspace_job_runner

FROM python:3.11-slim AS base

# Common system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    iproute2 \
    util-linux \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── deps layer ──────────────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── final image ─────────────────────────────────────────────────────────────
FROM base AS final

# Copy installed packages from deps stage.
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application source.
COPY . .

# Non-root user for security.
RUN useradd -m -u 1000 devnest && chown -R devnest:devnest /app
USER devnest

EXPOSE 8000

# Healthcheck: hit /ready which verifies DB connectivity.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -sf http://localhost:8000/ready || exit 1

# Default: run the API server.
# Override CMD in docker-compose to run worker or other entrypoints.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
