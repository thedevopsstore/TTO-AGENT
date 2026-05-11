# Build stage — install dependencies and generate lockfile
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Generate lockfile and install dependencies (without project source)
RUN --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv lock

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev --no-editable

# Copy source and install project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-editable

# Pre-warm uvx cache for mcp-proxy-for-aws so first request has no download delay
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_CACHE_DIR=/root/.cache/uv uvx mcp-proxy-for-aws@latest --help > /dev/null 2>&1 || true

# Clean bytecode cache
RUN find /app/.venv -name '__pycache__' -type d -exec rm -rf {} + && \
    find /app/.venv -name '*.pyc' -delete


# Final stage
FROM python:3.12-alpine

RUN adduser -D -h /home/app -s /bin/sh app
WORKDIR /app

# Copy virtual environment and project
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Copy uv/uvx binaries — needed at runtime to spawn mcp-proxy-for-aws subprocess
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=builder /usr/local/bin/uvx /usr/local/bin/uvx

# Copy pre-warmed uvx cache so mcp-proxy-for-aws starts without downloading
COPY --from=builder --chown=app:app /root/.cache/uv /home/app/.cache/uv

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/home/app/.cache/uv

USER app

EXPOSE 8000

ENTRYPOINT ["mcp-server-aws"]
