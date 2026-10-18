"""Tests of the database schema: migrations, constraints, cascades and the trigger."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from interpol_db import ArrestWarrant, Country, Notice, NoticePhoto, notice_nationalities


def insert_notice(connection: Connection, **values: Any) -> int:
    """Insert a notice with the minimum required fields and return its id."""
    row = {"entity_id": "2026/1", "name": "DOE", "content_hash": "hash-1", **values}
    notice_id: int = connection.execute(
        insert(Notice).values(row).returning(Notice.id)
    ).scalar_one()
    return notice_id


def test_migrations_round_trip_and_match_the_models(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    # Fails if the models describe something that the migrations do not create.
    command.check(alembic_config)


def test_countries_are_seeded_with_the_iso_list(connection: Connection) -> None:
    count = connection.execute(select(func.count()).select_from(Country)).scalar_one()
    turkey = connection.execute(select(Country.name).where(Country.code == "TR")).scalar_one()

    assert count == 249
    assert turkey == "Türkiye"


def test_notice_with_all_optional_fields_empty_is_accepted(connection: Connection) -> None:
    notice_id = insert_notice(connection)

    notice = connection.execute(select(Notice).where(Notice.id == notice_id)).one()
    assert notice.language_codes == []
    assert notice.created_at is not None


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("sex_id", "X"),
        ("height", Decimal("0")),
        ("height", Decimal("3.00")),
        ("weight", 0),
        ("weight", 500),
    ],
)
def test_check_constraints_reject_invalid_values(
    connection: Connection, column: str, value: object
) -> None:
    with pytest.raises(IntegrityError, match="ck_notices_"):
        insert_notice(connection, **{column: value})


def test_unknown_country_code_is_rejected(connection: Connection) -> None:
    with pytest.raises(IntegrityError, match="fk_notices_country_of_birth_code_countries"):
        insert_notice(connection, country_of_birth_code="QQ")


def test_upsert_on_entity_id_keeps_a_single_row(connection: Connection) -> None:
    for name in ("FIRST", "SECOND"):
        statement = pg_insert(Notice).values(entity_id="2026/1", name=name, content_hash="h")
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[Notice.entity_id], set_={"name": statement.excluded.name}
            )
        )

    names = connection.execute(select(Notice.name)).scalars().all()
    assert names == ["SECOND"]


def test_deleting_a_notice_deletes_its_children(connection: Connection) -> None:
    notice_id = insert_notice(connection)
    connection.execute(insert(notice_nationalities).values(notice_id=notice_id, country_code="TR"))
    connection.execute(
        insert(ArrestWarrant).values(notice_id=notice_id, issuing_country_code="US", charge="x")
    )
    connection.execute(insert(NoticePhoto).values(notice_id=notice_id, picture_id="123"))

    connection.execute(delete(Notice).where(Notice.id == notice_id))

    for child in (notice_nationalities, ArrestWarrant.__table__, NoticePhoto.__table__):
        assert connection.execute(select(func.count()).select_from(child)).scalar_one() == 0


def test_updated_at_ignores_an_update_of_last_seen_at_only(connection: Connection) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    notice_id = insert_notice(connection, updated_at=yesterday)

    connection.execute(update(Notice).where(Notice.id == notice_id).values(last_seen_at=func.now()))

    updated_at = connection.execute(select(Notice.updated_at)).scalar_one()
    assert updated_at == yesterday


def test_updated_at_changes_when_the_content_changes(connection: Connection) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    notice_id = insert_notice(connection, updated_at=yesterday)

    connection.execute(update(Notice).where(Notice.id == notice_id).values(name="RENAMED"))

    updated_at = connection.execute(select(Notice.updated_at)).scalar_one()
    assert updated_at > yesterday
