"""Tests of the face worker's queries against a real, migrated PostgreSQL container.

The fixtures live here and not in a conftest.py: mypy checks every test folder, and a
third top-level ``conftest`` module would clash with the other packages' ones. All
notices and photos are made up.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, delete, insert, select, text
from testcontainers.community.postgres import PostgresContainer

from interpol_db import Notice, NoticePhoto
from interpol_face_worker.repository import (
    MAX_DOWNLOAD_ATTEMPTS,
    PendingPhoto,
    PhotoRepository,
    next_pending_photo,
    record_download,
    record_failure,
    stored_object_keys,
)

# Same image as docker-compose.yml.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.2-pg18"
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "libs" / "db" / "alembic.ini"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


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


def add_notice(connection: Connection, entity_id: str, *, removed: bool = False) -> int:
    """Insert a made-up notice and return its id."""
    return connection.execute(
        insert(Notice)
        .values(
            entity_id=entity_id,
            name="TEST",
            content_hash="hash",
            deleted_at=NOW - timedelta(days=1) if removed else None,
        )
        .returning(Notice.id)
    ).scalar_one()


def add_photo(
    connection: Connection,
    notice_id: int,
    picture_id: str,
    *,
    attempts: int = 0,
    last_attempt_at: datetime | None = None,
    downloaded: bool = False,
) -> int:
    """Insert a photo row and return its id."""
    return connection.execute(
        insert(NoticePhoto)
        .values(
            notice_id=notice_id,
            picture_id=picture_id,
            download_attempts=attempts,
            last_attempt_at=last_attempt_at,
            object_key=f"notices/x/{picture_id}" if downloaded else None,
            sha256="abc" if downloaded else None,
            downloaded_at=NOW if downloaded else None,
        )
        .returning(NoticePhoto.id)
    ).scalar_one()


def photo_row(connection: Connection, photo_id: int) -> tuple[object, ...]:
    row = connection.execute(
        select(
            NoticePhoto.object_key,
            NoticePhoto.sha256,
            NoticePhoto.downloaded_at,
            NoticePhoto.download_attempts,
            NoticePhoto.last_attempt_at,
        ).where(NoticePhoto.id == photo_id)
    ).one()
    return tuple(row)


def test_a_new_photo_is_pending_with_its_entity_id(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    photo_id = add_photo(connection, notice_id, "111")

    assert next_pending_photo(connection, NOW) == PendingPhoto(
        photo_id=photo_id, entity_id="1900/1", picture_id="111", download_attempts=0
    )


def test_nothing_is_pending_in_an_empty_table(connection: Connection) -> None:
    assert next_pending_photo(connection, NOW) is None


def test_downloaded_photos_and_removed_notices_are_skipped(connection: Connection) -> None:
    add_photo(connection, add_notice(connection, "1900/1"), "111", downloaded=True)
    add_photo(connection, add_notice(connection, "1900/2", removed=True), "222")

    assert next_pending_photo(connection, NOW) is None


def test_never_tried_photos_come_before_retries(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    add_photo(connection, notice_id, "111", attempts=1, last_attempt_at=NOW - timedelta(days=1))
    fresh = add_photo(connection, notice_id, "222")

    pending = next_pending_photo(connection, NOW)

    assert pending is not None
    assert pending.photo_id == fresh


def test_the_oldest_attempt_is_retried_first(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    add_photo(connection, notice_id, "111", attempts=1, last_attempt_at=NOW - timedelta(hours=3))
    oldest = add_photo(
        connection, notice_id, "222", attempts=1, last_attempt_at=NOW - timedelta(hours=5)
    )

    pending = next_pending_photo(connection, NOW)

    assert pending is not None
    assert pending.photo_id == oldest


@pytest.mark.parametrize(
    ("attempts", "delay"),
    [(1, timedelta(hours=1)), (2, timedelta(hours=2)), (3, timedelta(hours=4))],
)
def test_a_failed_photo_waits_a_doubling_delay(
    connection: Connection, attempts: int, delay: timedelta
) -> None:
    notice_id = add_notice(connection, "1900/1")
    last_attempt = NOW - delay
    add_photo(connection, notice_id, "111", attempts=attempts, last_attempt_at=last_attempt)

    # One second before the delay has passed: not yet; exactly when it passed: due.
    assert next_pending_photo(connection, NOW - timedelta(seconds=1)) is None
    assert next_pending_photo(connection, NOW) is not None


def test_a_photo_is_given_up_after_the_last_attempt(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    long_ago = NOW - timedelta(days=30)
    add_photo(
        connection, notice_id, "111", attempts=MAX_DOWNLOAD_ATTEMPTS, last_attempt_at=long_ago
    )

    assert next_pending_photo(connection, NOW) is None


def test_record_download_fills_the_file_columns(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")

    assert record_download(connection, photo_id, "notices/1900-1/111", "f00d", NOW) is True

    assert photo_row(connection, photo_id)[:3] == ("notices/1900-1/111", "f00d", NOW)
    assert next_pending_photo(connection, NOW) is None


def test_record_download_of_a_deleted_row_reports_false(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")
    # The ingestor drops the photo while it is being downloaded.
    connection.execute(delete(NoticePhoto).where(NoticePhoto.id == photo_id))

    assert record_download(connection, photo_id, "notices/1900-1/111", "f00d", NOW) is False


def test_record_download_does_not_overwrite_an_earlier_download(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111", downloaded=True)

    assert record_download(connection, photo_id, "notices/other", "beef", NOW) is False
    assert photo_row(connection, photo_id)[0] == "notices/x/111"


def test_record_failure_counts_the_attempt(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")

    record_failure(connection, photo_id, NOW)
    record_failure(connection, photo_id, NOW + timedelta(hours=1))

    assert photo_row(connection, photo_id)[3:] == (2, NOW + timedelta(hours=1))


def test_stored_object_keys_lists_only_downloaded_photos(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    add_photo(connection, notice_id, "111", downloaded=True)
    add_photo(connection, notice_id, "222")

    assert stored_object_keys(connection) == {"notices/x/111"}


def test_photo_repository_commits_each_call(engine: Engine) -> None:
    """The service's view: every call runs and commits in its own transaction."""
    repository = PhotoRepository(engine.begin)
    try:
        with engine.begin() as connection:
            photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")

        assert repository.next_pending_photo(NOW) is not None
        repository.record_failure(photo_id, NOW - timedelta(hours=2))
        assert repository.record_download(photo_id, "notices/1900-1/111", "f00d", NOW) is True
        assert repository.next_pending_photo(NOW) is None
        assert repository.stored_object_keys() == {"notices/1900-1/111"}
    finally:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE notices CASCADE"))
