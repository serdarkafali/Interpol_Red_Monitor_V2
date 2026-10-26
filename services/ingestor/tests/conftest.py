"""Fixtures that run the ingestor against a real, migrated PostgreSQL container.

The schema comes from the migrations, as in ``libs/db/tests``, so the queries are tested
against exactly what production runs.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer

# Same image as docker-compose.yml.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.2-pg18"
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "libs" / "db" / "alembic.ini"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine of a throwaway database migrated to the latest revision."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        with pytest.MonkeyPatch.context() as monkeypatch:
            # env.py prefers DATABASE_URL, so the development database is never touched.
            monkeypatch.setenv("DATABASE_URL", url)
            command.upgrade(Config(ALEMBIC_INI), "head")
        engine = create_engine(url)
        yield engine
        engine.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back after the test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def committing_engine(engine: Engine) -> Iterator[Engine]:
    """The engine for code that commits by itself; its notices are removed afterwards."""
    yield engine
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE notices CASCADE"))
