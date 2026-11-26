# syntax=docker/dockerfile:1
# Build from the repository root: docker compose builds it with context "." (see
# docker-compose.yml), because the web interface needs the shared libs next to it.

# --- build stage: install the web interface and its libs into a virtual environment --
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY services ./services
# --frozen: install exactly what uv.lock says; --no-editable: copy the workspace
# packages (with their templates and static files) into the environment, so the
# runtime image does not need /src.
RUN uv sync --frozen --no-dev --no-editable --package interpol-web

# --- runtime stage: only the environment, run as an unprivileged user -----------------
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home web
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
USER web
EXPOSE 8000
# Docker asks /healthz, which needs no login and shows no data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["interpol-web"]
