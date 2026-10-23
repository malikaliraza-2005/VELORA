# syntax=docker/dockerfile:1
# The VELORA application image (P12-T2): one image for the API and dashboard, the worker, the
# monitor, the backup job and the founder's tools; compose picks the command.
# Build from the repository root: docker build -f infra/docker/app.Dockerfile .
# Base images are pinned by digest (multi-arch: amd64 and the arm64 of free Ampere VMs).

ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

FROM ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app/backend
# Dependencies first (from the lockfile only), so code changes reuse this layer.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/README.md ./
COPY backend/src ./src
# Installed in place: the dashboard is found at /app/web next to /app/backend (api/web.py).
RUN uv sync --locked --no-dev

FROM ${PYTHON_IMAGE}
# PostgreSQL 17 client tools for backups and restores (P11), from the PostgreSQL project's
# own repository; its signing key is checked against the published fingerprint. /work and
# /backups are the backup job's volume mount points: a new volume takes their ownership.
ARG PGDG_KEY_FINGERPRINT=B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /tmp/pgdg.asc; \
    gpg --batch --show-keys --with-colons /tmp/pgdg.asc | grep -q "^fpr:::::::::${PGDG_KEY_FINGERPRINT}:"; \
    gpg --batch --dearmor -o /usr/share/keyrings/pgdg.gpg /tmp/pgdg.asc; \
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-17; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/* /tmp/pgdg.asc; \
    pg_dump --version | grep -q ' 17\.'; \
    useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin velora; \
    install -d -o 10001 -g 10001 -m 700 /work /backups

COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/backend/src /app/backend/src
COPY web /app/web

ARG VELORA_REVISION=unknown
LABEL org.opencontainers.image.title="velora-app" \
      org.opencontainers.image.revision="${VELORA_REVISION}"
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VELORA_REVISION=${VELORA_REVISION}
WORKDIR /app
USER 10001:10001
CMD ["python", "-m", "velora.serve"]
