# ═══════════════════════════════════════════════════════════════════
# Re:putation — Backend image (INFRA-6)
#   - Multi-stage: build toolchain stays in the builder, not the runtime.
#   - Base pinned by digest for reproducible rebuilds (python:3.11-slim).
#   - Runs as a non-root user.
#   - HEALTHCHECK hits /health/live.
# Pinned tag for readability; the digest is the source of truth.
# ═══════════════════════════════════════════════════════════════════

# ── Builder stage ──────────────────────────────────────────────────
FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0 AS builder
WORKDIR /app

# Build-time toolchain + dev headers (compilers, -dev packages). These stay in
# the builder and are NOT copied into the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libffi-dev libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies into an isolated virtualenv so we can copy just the venv
# into the slim runtime stage.
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT="$VIRTUAL_ENV"

# Production must resolve the exact dependency graph that passed CI. Installing
# directly from broad pyproject lower bounds can silently pull breaking major SDK
# releases even when uv.lock was reviewed and audited.
RUN pip install --no-cache-dir "uv==0.8.13"
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
RUN uv sync --locked --no-dev --no-editable

# ── Runtime stage ──────────────────────────────────────────────────
FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0 AS runtime
WORKDIR /app

# Runtime-only shared libraries:
#   - WeasyPrint: libpango / libpangoft2 / libharfbuzz / libjpeg / libopenjp2
#   - fonts-nanum: Korean (CJK) glyphs so the V0/monthly PDF reports render
#     Hangul instead of tofu boxes (the slim base ships only Latin fonts).
#   - cffi: libffi8        - curl: HEALTHCHECK probe
# psycopg2-binary bundles its own libpq, so no system libpq is needed.
# No compilers or -dev headers here (those stayed in the builder).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libjpeg62-turbo libopenjp2-7 \
    libffi8 \
    curl \
    fonts-nanum \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# Bring in the prebuilt virtualenv and the application code.
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Non-root user + writable runtime dirs.
RUN useradd -m appuser \
    && mkdir -p /tmp/private_asset_uploads /tmp/sites /tmp/reports \
    && chown -R appuser:appuser /app /tmp/private_asset_uploads /tmp/sites /tmp/reports
USER appuser

ENV PYTHONPATH=/app

# Parity with docker-compose; Cloud Run uses its own probes. Only meaningful for
# the api SERVICE (worker/beat expose no HTTP server) — non-api containers will
# report unhealthy locally, which is expected.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health/live" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
