# syntax=docker/dockerfile:1
# Image of the one-off "migrate" step (ADR 0004, ADR 0008): applies the Alembic
# migrations and exits. Build from the repository root (context "." in
# docker-compose.yml). The connection URL comes from the DATABASE_URL variable.

# --- build stage: install interpol-db with Alembic into a virtual environment ---------
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
RUN uv sync --frozen --no-dev --no-editable --package interpol-db

# --- runtime stage: the environment plus alembic.ini, which points at the migrations --
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home migrate
COPY --from=build /app/.venv /app/.venv
# alembic.ini finds the migrations relative to itself (src/interpol_db/migrations), so
# that folder is copied next to it; the installed package provides env.py's imports.
COPY libs/db/alembic.ini /app/db/alembic.ini
COPY libs/db/src/interpol_db/migrations /app/db/src/interpol_db/migrations
WORKDIR /app/db
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
USER migrate
CMD ["alembic", "upgrade", "head"]
