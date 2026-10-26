"""Database writes for the three notice events (ADR 0008).

Every function works on a ``Connection`` inside a transaction owned by the caller, so one
Kafka message is written all or nothing. Nothing here commits. Every write is idempotent:
applying the same event twice leaves the database as applying it once.
"""

import enum
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, Connection, delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert

from interpol_db import ArrestWarrant, Country, Notice, NoticePhoto, notice_nationalities
from interpol_ingestor.normalize import NoticeRecord


class ObservedOutcome(enum.Enum):
    """What applying a ``notice.observed`` event did."""

    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class ObservedResult:
    """The outcome and the country codes that had to be added to the countries table."""

    outcome: ObservedOutcome
    added_country_codes: frozenset[str] = frozenset()


def apply_observed(
    connection: Connection,
    record: NoticeRecord,
    occurred_at: datetime,
    *,
    ignore_content_hash: bool = False,
) -> ObservedResult:
    """Insert or update a notice with its nationalities, warrants and photos.

    Nothing is written if the stored ``content_hash`` equals the record's, unless
    ``ignore_content_hash`` is set (used to replay events after a normalisation fix).
    """
    if not ignore_content_hash:
        stored_hash = connection.execute(
            select(Notice.content_hash).where(Notice.entity_id == record.entity_id)
        ).scalar_one_or_none()
        if stored_hash == record.content_hash:
            return ObservedResult(ObservedOutcome.UNCHANGED)

    added_codes = _ensure_countries(connection, record.country_codes)
    notice_id, inserted = _upsert_notice(connection, record, occurred_at)
    _replace_nationalities(connection, notice_id, record.nationality_codes)
    _replace_arrest_warrants(connection, notice_id, record)
    _sync_photos(connection, notice_id, record.picture_ids)
    outcome = ObservedOutcome.INSERTED if inserted else ObservedOutcome.UPDATED
    return ObservedResult(outcome, added_codes)


def apply_seen(connection: Connection, entity_id: str, occurred_at: datetime) -> bool:
    """Record a list sighting; return whether a removed notice came back.

    A notice removed after ``occurred_at`` stays removed, so an old event processed
    again cannot undo a newer removal. Unknown entity ids are ignored.
    """
    revived = connection.execute(
        update(Notice)
        .where(
            Notice.entity_id == entity_id,
            Notice.deleted_at.is_not(None),
            Notice.deleted_at <= occurred_at,
        )
        .values(deleted_at=None)
        .returning(Notice.id)
    ).first()
    connection.execute(
        update(Notice)
        .where(Notice.entity_id == entity_id)
        .values(last_seen_at=func.greatest(Notice.last_seen_at, occurred_at))
    )
    return revived is not None


def apply_removed(connection: Connection, entity_id: str, occurred_at: datetime) -> bool:
    """Soft delete a notice; return whether this call deleted it.

    An already removed notice keeps its first removal time, and a notice seen after
    ``occurred_at`` is not removed. Unknown entity ids are ignored.
    """
    removed = connection.execute(
        update(Notice)
        .where(
            Notice.entity_id == entity_id,
            Notice.deleted_at.is_(None),
            Notice.last_seen_at <= occurred_at,
        )
        .values(deleted_at=occurred_at)
        .returning(Notice.id)
    ).first()
    return removed is not None


def _ensure_countries(connection: Connection, codes: frozenset[str]) -> frozenset[str]:
    """Add missing country codes with an empty name; return the codes that were added."""
    if not codes:
        return frozenset()
    added = connection.execute(
        insert(Country)
        .values([{"code": code} for code in sorted(codes)])
        .on_conflict_do_nothing(index_elements=[Country.code])
        .returning(Country.code)
    ).scalars()
    return frozenset(added)


def _upsert_notice(
    connection: Connection, record: NoticeRecord, occurred_at: datetime
) -> tuple[int, bool]:
    """Insert or update the notices row; return its id and whether it was inserted."""
    content = {
        "name": record.name,
        "forename": record.forename,
        "sex_id": record.sex_id,
        "date_of_birth": record.date_of_birth,
        "place_of_birth": record.place_of_birth,
        "country_of_birth_code": record.country_of_birth_code,
        "height": record.height,
        "weight": record.weight,
        "distinguishing_marks": record.distinguishing_marks,
        "language_codes": list(record.language_codes),
        "eye_color_codes": list(record.eye_color_codes),
        "hair_color_codes": list(record.hair_color_codes),
        "thumbnail_picture_id": record.thumbnail_picture_id,
        "content_hash": record.content_hash,
    }
    notice_id, inserted = connection.execute(
        insert(Notice)
        .values(entity_id=record.entity_id, last_seen_at=occurred_at, **content)
        # last_seen_at and deleted_at belong to the list scan, created_at to the insert.
        .on_conflict_do_update(index_elements=[Notice.entity_id], set_=content)
        # xmax is 0 only for a row version created by an insert (PostgreSQL detail).
        .returning(Notice.id, literal_column("xmax = 0", Boolean))
    ).one()
    return notice_id, inserted


def _replace_nationalities(connection: Connection, notice_id: int, codes: tuple[str, ...]) -> None:
    connection.execute(
        delete(notice_nationalities).where(notice_nationalities.c.notice_id == notice_id)
    )
    if codes:
        connection.execute(
            notice_nationalities.insert(),
            [{"notice_id": notice_id, "country_code": code} for code in codes],
        )


def _replace_arrest_warrants(connection: Connection, notice_id: int, record: NoticeRecord) -> None:
    # Warrants have no natural key, so they cannot be matched one by one.
    connection.execute(delete(ArrestWarrant).where(ArrestWarrant.notice_id == notice_id))
    if record.arrest_warrants:
        connection.execute(
            insert(ArrestWarrant),
            [
                {
                    "notice_id": notice_id,
                    "issuing_country_code": warrant.issuing_country_code,
                    "charge": warrant.charge,
                    "charge_translation": warrant.charge_translation,
                }
                for warrant in record.arrest_warrants
            ],
        )


def _sync_photos(connection: Connection, notice_id: int, picture_ids: tuple[str, ...]) -> None:
    """Add new photos and drop vanished ones; kept rows keep their download data."""
    connection.execute(
        delete(NoticePhoto).where(
            NoticePhoto.notice_id == notice_id, NoticePhoto.picture_id.not_in(picture_ids)
        )
    )
    if picture_ids:
        connection.execute(
            insert(NoticePhoto)
            .values([{"notice_id": notice_id, "picture_id": pid} for pid in picture_ids])
            .on_conflict_do_nothing(index_elements=[NoticePhoto.picture_id])
        )
