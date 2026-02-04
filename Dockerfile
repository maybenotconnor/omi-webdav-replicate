# syntax=docker/dockerfile:1

# Build stage
FROM python:3.12-slim-bookworm AS builder

# Copy UV binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# UV environment variables for Docker
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (for layer caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev

# Copy application code
COPY sync.py .
COPY pyproject.toml .
COPY uv.lock .

# Install the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# Runtime stage
FROM python:3.12-slim-bookworm

# Install tini for proper signal handling
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment and application from builder
COPY --from=builder /app /app

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Ensure Python output is sent straight to terminal
ENV PYTHONUNBUFFERED=1

# Create state directory
RUN mkdir -p /app/state

# Use tini as init process
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the sync service
CMD ["python", "sync.py"]
