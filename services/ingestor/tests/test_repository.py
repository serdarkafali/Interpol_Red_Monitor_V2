"""Tests of the notice writes against a real, migrated PostgreSQL container.

The schema comes from the migrations, as in ``libs/db/tests``, so the queries are tested
against exactly what production runs. The fixtures live in this module rather than in a
``conftest.py``: mypy checks every test folder, and a second top-level ``conftest``
module would clash with the one in ``libs/db/tests``.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, Row, create_engine, select, update
from testcontainers.community.postgres import PostgresContainer

from interpol_db import ArrestWarrant, Country, Notice, NoticePhoto, notice_nationalities
from interpol_ingestor.normalize import NoticeRecord, WarrantRecord
from interpol_ingestor.repository import (
    ObservedOutcome,
    apply_observed,
    apply_removed,
    apply_seen,
)

# Same image as docker-compose.yml.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.2-pg18"
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "libs" / "db" / "alembic.ini"

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)

# Made-up person; every value is invented for the tests.
RECORD = NoticeRecord(
    entity_id="2026/1",
    content_hash="a" * 64,
    name="TESTER",
    forename="SAMPLE",
    sex_id="M",
    date_of_birth=date(1980, 5, 17),
    place_of_birth=None,
    country_of_birth_code="DE",
    height=Decimal("1.80"),
    weight=72,
    distinguishing_marks=None,
    language_codes=("GER",),
    eye_color_codes=(),
    hair_color_codes=(),
    thumbnail_picture_id=None,
    nationality_codes=("DE", "FR"),
    arrest_warrants=(
        WarrantRecord(issuing_country_code="NL", charge="Made-up charge", charge_translation=None),
    ),
    picture_ids=("101", "102"),
)


@pytest.fixture(scope="module")
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


def notice(connection: Connection, entity_id: str = "2026/1") -> Row[Any]:
    """The notices row; a Core connection returns rows, not ORM objects."""
    return connection.execute(select(Notice).where(Notice.entity_id == entity_id)).one()


def nationalities(connection: Connection) -> list[str]:
    return list(
        connection.execute(
            select(notice_nationalities.c.country_code).order_by(
                notice_nationalities.c.country_code
            )
        ).scalars()
    )


def warrant_countries(connection: Connection) -> list[str]:
    return list(connection.execute(select(ArrestWarrant.issuing_country_code)).scalars())


def photos(connection: Connection) -> dict[str, str | None]:
    rows = connection.execute(select(NoticePhoto.picture_id, NoticePhoto.object_key)).all()
    return {picture_id: object_key for picture_id, object_key in rows}


# --- notice.observed ------------------------------------------------------------------


def test_a_new_notice_is_inserted_with_its_children(connection: Connection) -> None:
    result = apply_observed(connection, RECORD, T0)

    stored = notice(connection)
    assert result.outcome is ObservedOutcome.INSERTED
    assert result.added_country_codes == frozenset()
    assert (stored.name, stored.height, stored.weight) == ("TESTER", Decimal("1.80"), 72)
    assert stored.language_codes == ["GER"]
    assert stored.last_seen_at == T0
    assert stored.deleted_at is None
    assert nationalities(connection) == ["DE", "FR"]
    assert warrant_countries(connection) == ["NL"]
    assert photos(connection) == {"101": None, "102": None}


def test_the_same_content_is_not_written_again(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)
    before = notice(connection).updated_at

    result = apply_observed(connection, RECORD, T0 + HOUR)

    assert result.outcome is ObservedOutcome.UNCHANGED
    assert notice(connection).updated_at == before


def test_ignoring_the_content_hash_rewrites_the_notice(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)

    result = apply_observed(connection, RECORD, T0, ignore_content_hash=True)

    assert result.outcome is ObservedOutcome.UPDATED
    assert nationalities(connection) == ["DE", "FR"]


def test_changed_content_replaces_the_children_and_keeps_lifecycle_columns(
    connection: Connection,
) -> None:
    apply_observed(connection, RECORD, T0)
    connection.execute(
        update(NoticePhoto).where(NoticePhoto.picture_id == "101").values(object_key="k/101")
    )
    connection.execute(update(Notice).values(deleted_at=T0))
    created_at = notice(connection).created_at
    changed = replace(
        RECORD,
        content_hash="b" * 64,
        weight=None,
        nationality_codes=("TR",),
        arrest_warrants=(
            WarrantRecord(issuing_country_code="BE", charge="One", charge_translation=None),
            WarrantRecord(issuing_country_code="AT", charge="Two", charge_translation="Zwei"),
        ),
        picture_ids=("101", "103"),
    )

    result = apply_observed(connection, changed, T0 + HOUR)

    stored = notice(connection)
    assert result.outcome is ObservedOutcome.UPDATED
    assert stored.weight is None
    assert stored.content_hash == "b" * 64
    # Lifecycle columns belong to the list scan and to the first insert.
    assert (stored.created_at, stored.last_seen_at, stored.deleted_at) == (created_at, T0, T0)
    assert nationalities(connection) == ["TR"]
    assert sorted(warrant_countries(connection)) == ["AT", "BE"]
    # 101 keeps its download data, 102 is gone, 103 is new.
    assert photos(connection) == {"101": "k/101", "103": None}


def test_an_unknown_country_code_is_added_with_an_empty_name(connection: Connection) -> None:
    result = apply_observed(connection, replace(RECORD, nationality_codes=("DE", "QZ")), T0)

    name = connection.execute(select(Country.name).where(Country.code == "QZ")).scalar_one()
    assert result.added_country_codes == {"QZ"}
    assert name is None
    assert nationalities(connection) == ["DE", "QZ"]


def test_a_notice_without_children_is_stored(connection: Connection) -> None:
    empty = replace(
        RECORD, country_of_birth_code=None, nationality_codes=(), arrest_warrants=(), picture_ids=()
    )

    apply_observed(connection, empty, T0)

    assert (nationalities(connection), warrant_countries(connection), photos(connection)) == (
        [],
        [],
        {},
    )


# --- notice.seen ----------------------------------------------------------------------


def test_seen_moves_last_seen_forward_but_never_back(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)

    apply_seen(connection, "2026/1", T0 + HOUR)
    apply_seen(connection, "2026/1", T0 - HOUR)

    assert notice(connection).last_seen_at == T0 + HOUR


def test_seen_revives_a_notice_removed_before_it(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)
    apply_removed(connection, "2026/1", T0 + HOUR)

    revived = apply_seen(connection, "2026/1", T0 + 2 * HOUR)

    assert revived is True
    assert notice(connection).deleted_at is None


def test_an_old_seen_does_not_undo_a_newer_removal(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)
    apply_removed(connection, "2026/1", T0 + 2 * HOUR)

    revived = apply_seen(connection, "2026/1", T0 + HOUR)

    assert revived is False
    assert notice(connection).deleted_at == T0 + 2 * HOUR


def test_seen_for_an_unknown_notice_does_nothing(connection: Connection) -> None:
    assert apply_seen(connection, "2026/99", T0) is False


# --- notice.removed -------------------------------------------------------------------


def test_removed_keeps_the_first_removal_time(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)

    first = apply_removed(connection, "2026/1", T0 + HOUR)
    second = apply_removed(connection, "2026/1", T0 + 2 * HOUR)

    assert (first, second) == (True, False)
    assert notice(connection).deleted_at == T0 + HOUR


def test_removed_is_ignored_when_the_notice_was_seen_later(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)
    apply_seen(connection, "2026/1", T0 + 2 * HOUR)

    assert apply_removed(connection, "2026/1", T0 + HOUR) is False
    assert notice(connection).deleted_at is None


def test_removed_for_an_unknown_notice_does_nothing(connection: Connection) -> None:
    assert apply_removed(connection, "2026/99", T0) is False


def test_observed_does_not_revive_a_removed_notice(connection: Connection) -> None:
    apply_observed(connection, RECORD, T0)
    apply_removed(connection, "2026/1", T0 + HOUR)

    apply_observed(connection, replace(RECORD, content_hash="c" * 64), T0 + 2 * HOUR)

    assert notice(connection).deleted_at == T0 + HOUR
