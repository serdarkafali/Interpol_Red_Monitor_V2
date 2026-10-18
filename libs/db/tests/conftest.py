"""Fixtures that apply the real migrations to a throwaway PostgreSQL container.

The schema under test is the one the migrations build, not ``Base.metadata.create_all``:
production databases are only ever changed by migrations, so that is what is tested.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine
from testcontainers.community.postgres import PostgresContainer

# Same image as docker-compose.yml, so that tests run on the same PostgreSQL version.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.2-pg18"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Start one PostgreSQL container for the whole test session."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Iterator[Config]:
    """Alembic configuration pointing at the test container.

    ``env.py`` reads ``DATABASE_URL`` from the environment before the ``.env`` file, so
    setting it here guarantees that migrations never touch the development database.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DATABASE_URL", database_url)
        yield Config(ALEMBIC_INI)


@pytest.fixture(scope="session")
def engine(database_url: str, alembic_config: Config) -> Iterator[Engine]:
    """Engine of a database migrated to the latest revision."""
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back, so tests never see each other."""
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
