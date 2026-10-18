"""Alembic environment: connects to the database and runs the migrations.

The connection URL comes from the ``DATABASE_URL`` environment variable or from the
repository's ``.env`` file; it is never written to ``alembic.ini``.
"""

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, pool

from interpol_db import Base

# libs/db/src/interpol_db/migrations/env.py -> repository root is five levels up.
_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[5] / ".env"


class MigrationSettings(BaseSettings):
    """Settings needed to run migrations, validated before connecting."""

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline(url: str) -> None:
    """Emit the migration SQL to stdout instead of executing it (``--sql``)."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online(url: str) -> None:
    """Connect to the database and apply the migrations inside a transaction."""
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


database_url = MigrationSettings().database_url  # type: ignore[call-arg]
if context.is_offline_mode():
    run_migrations_offline(database_url)
else:
    run_migrations_online(database_url)
