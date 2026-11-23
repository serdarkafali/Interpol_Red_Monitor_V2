"""Database reads and writes of the face worker (ADR 0011).

The photos to download are chosen from ``notice_photos``, not from Kafka. Every function
works on a ``Connection`` inside a transaction owned by the caller; nothing here commits.
The face worker writes only the download columns; rows are added and deleted by the
ingestor.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, Integer, cast, func, literal, or_, select, update

from interpol_db import Notice, NoticePhoto

# A function that opens a transaction and yields its connection, e.g. ``engine.begin``.
Transaction = Callable[[], AbstractContextManager[Connection]]

# After this many failed downloads a photo is not tried again (ADR 0011).
MAX_DOWNLOAD_ATTEMPTS = 5
# The wait before the next attempt doubles with every failure: 1, 2, 4, 8 hours.
FIRST_RETRY_DELAY_HOURS = 1


@dataclass(frozen=True)
class PendingPhoto:
    """A photo that should be downloaded now."""

    photo_id: int
    entity_id: str
    picture_id: str
    download_attempts: int


def next_pending_photo(connection: Connection, now: datetime) -> PendingPhoto | None:
    """Return the next photo to download, or None if nothing is due.

    Photos never tried come first, then the oldest attempt. A photo is skipped while its
    notice is removed, after the last allowed attempt, and until its retry delay passed.
    """
    # make_interval(years, months, weeks, days, hours): 1, 2, 4, 8 hours after 1-4 failures.
    retry_hours = FIRST_RETRY_DELAY_HOURS * cast(
        func.power(2, NoticePhoto.download_attempts - 1), Integer
    )
    retry_delay = func.make_interval(0, 0, 0, 0, retry_hours)
    statement = (
        select(
            NoticePhoto.id,
            Notice.entity_id,
            NoticePhoto.picture_id,
            NoticePhoto.download_attempts,
        )
        .join(Notice, Notice.id == NoticePhoto.notice_id)
        .where(
            NoticePhoto.downloaded_at.is_(None),
            Notice.deleted_at.is_(None),
            NoticePhoto.download_attempts < MAX_DOWNLOAD_ATTEMPTS,
            or_(
                NoticePhoto.last_attempt_at.is_(None),
                NoticePhoto.last_attempt_at <= literal(now) - retry_delay,
            ),
        )
        .order_by(NoticePhoto.last_attempt_at.asc().nulls_first(), NoticePhoto.id)
        .limit(1)
    )
    row = connection.execute(statement).first()
    if row is None:
        return None
    return PendingPhoto(
        photo_id=row.id,
        entity_id=row.entity_id,
        picture_id=row.picture_id,
        download_attempts=row.download_attempts,
    )


def record_download(
    connection: Connection, photo_id: int, object_key: str, sha256: str, now: datetime
) -> bool:
    """Mark a photo as downloaded; return False if its row is gone or already downloaded.

    The row can be deleted by the ingestor while the photo is being downloaded; the file
    written for it is then removed by the daily cleanup.
    """
    result = connection.execute(
        update(NoticePhoto)
        .where(NoticePhoto.id == photo_id, NoticePhoto.downloaded_at.is_(None))
        .values(object_key=object_key, sha256=sha256, downloaded_at=now)
    )
    return result.rowcount == 1


def record_failure(connection: Connection, photo_id: int, now: datetime) -> None:
    """Count one failed download attempt."""
    connection.execute(
        update(NoticePhoto)
        .where(NoticePhoto.id == photo_id)
        .values(download_attempts=NoticePhoto.download_attempts + 1, last_attempt_at=now)
    )


def stored_object_keys(connection: Connection) -> set[str]:
    """Every object key the database refers to; files not in this set are orphans."""
    keys = connection.execute(
        select(NoticePhoto.object_key).where(NoticePhoto.object_key.is_not(None))
    ).scalars()
    return {key for key in keys if key is not None}


class PhotoRepository:
    """The functions above, each run in its own short transaction.

    The service holds no transaction while a photo is downloaded (ADR 0011), so every
    call commits on its own.
    """

    def __init__(self, transaction: Transaction) -> None:
        self._transaction = transaction

    def next_pending_photo(self, now: datetime) -> PendingPhoto | None:
        with self._transaction() as connection:
            return next_pending_photo(connection, now)

    def record_download(self, photo_id: int, object_key: str, sha256: str, now: datetime) -> bool:
        with self._transaction() as connection:
            return record_download(connection, photo_id, object_key, sha256, now)

    def record_failure(self, photo_id: int, now: datetime) -> None:
        with self._transaction() as connection:
            record_failure(connection, photo_id, now)

    def stored_object_keys(self) -> set[str]:
        with self._transaction() as connection:
            return stored_object_keys(connection)
