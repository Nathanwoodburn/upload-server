### Build stage ###
FROM python:3.13-alpine AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.21 /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace

### Runtime stage ###
FROM python:3.13-alpine AS runtime
ENV PATH="/app/.venv/bin:$PATH"

# Install curl for healthchecks
RUN apk add --no-cache curl
WORKDIR /app

# Copy the virtual environment from build stage
COPY --from=build /app/.venv /app/.venv

# Copy all top-level Python files
COPY . /app

# Optional mount for data
#ENV BASE_DIR=/data
#VOLUME /data

EXPOSE 5000

ENTRYPOINT ["python3", "main.py"]
