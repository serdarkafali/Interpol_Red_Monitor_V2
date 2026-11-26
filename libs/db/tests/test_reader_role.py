"""Tests of the read-only role and the web interface's login user (ADR 0013).

The password below is made up. It contains a quote and a percent sign on purpose: both
would break a statement built by plain string formatting.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, insert, make_url, select, text
from sqlalchemy.exc import ProgrammingError

from interpol_db import Notice
from interpol_db.reader_user import READER_ROLE, ensure_reader_user

USER = "web_test"
PASSWORD = "it's-50%-made-up"


@pytest.fixture
def reader_engine(engine: Engine, database_url: str) -> Iterator[Engine]:
    """An engine that logs in as the read-only user."""
    with engine.begin() as connection:
        ensure_reader_user(connection, USER, PASSWORD)
    url = make_url(database_url).set(username=USER, password=PASSWORD)
    reader = create_engine(url)
    yield reader
    reader.dispose()


def test_the_reader_user_can_read_every_table(reader_engine: Engine) -> None:
    with reader_engine.connect() as connection:
        connection.execute(select(Notice.id).limit(1)).all()
        tables = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                " AND NOT has_table_privilege(table_schema || '.' || table_name, 'SELECT')"
            )
        ).scalar_one()

    assert tables == 0


def test_the_reader_user_cannot_write(reader_engine: Engine) -> None:
    with reader_engine.connect() as connection, pytest.raises(ProgrammingError) as error:
        connection.execute(insert(Notice).values(entity_id="1900/1", name="X", content_hash="h"))

    # 42501: insufficient_privilege
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


def test_the_reader_user_cannot_change_the_schema(reader_engine: Engine) -> None:
    with reader_engine.connect() as connection, pytest.raises(ProgrammingError):
        connection.execute(text("CREATE TABLE intruder (id int)"))


def test_running_it_again_updates_the_password_and_keeps_the_role(
    engine: Engine, database_url: str
) -> None:
    with engine.begin() as connection:
        ensure_reader_user(connection, USER, PASSWORD)
        created_again = ensure_reader_user(connection, USER, "a-new-made-up-password")

    assert created_again is False
    new_url = make_url(database_url).set(username=USER, password="a-new-made-up-password")
    reader = create_engine(new_url)
    try:
        with reader.connect() as connection:
            member = connection.execute(
                text("SELECT pg_has_role(current_user, :role, 'MEMBER')"), {"role": READER_ROLE}
            ).scalar_one()
    finally:
        reader.dispose()
    assert member is True
    # Put the password back for the other tests in this session.
    with engine.begin() as connection:
        ensure_reader_user(connection, USER, PASSWORD)


def test_a_name_that_needs_quoting_is_handled_safely(engine: Engine) -> None:
    odd_name = 'web"; DROP TABLE notices; --'

    with engine.begin() as connection:
        # Roles belong to the whole server, so it may exist from an earlier run.
        ensure_reader_user(connection, odd_name, PASSWORD)
        exists = connection.execute(
            text("SELECT count(*) FROM pg_roles WHERE rolname = :name"), {"name": odd_name}
        ).scalar_one()
        notices_left = connection.execute(text("SELECT to_regclass('public.notices')")).scalar()

    assert exists == 1
    assert notices_left is not None
