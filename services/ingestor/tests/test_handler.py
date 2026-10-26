"""Tests of one message → one transaction, and of the failure classification."""

from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import Engine, create_engine, select
from structlog.testing import capture_logs

from interpol_contracts import (
    NoticeEvent,
    NoticeObserved,
    NoticeRemoved,
    NoticeSeen,
    NoticeSnapshot,
    compute_content_hash,
    serialize_event,
)
from interpol_db import Notice
from interpol_ingestor.handler import DeadLetterError, EventHandler, TransientError

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SCAN_ID = uuid4()
# Made-up person; every value is invented for the tests.
DETAIL: dict[str, Any] = {
    "name": "TESTER",
    "sex_id": "M",
    "height": 0,
    "nationalities": ["DE"],
    "arrest_warrants": [{"issuing_country_id": "NL", "charge": "Made-up charge"}],
}


def observed(**changes: Any) -> NoticeObserved:
    detail = {**DETAIL, **changes}
    snapshot = NoticeSnapshot.model_validate(
        {
            "detail": detail,
            "picture_ids": ["101"],
            "content_hash": compute_content_hash(detail, ["101"]),
        }
    )
    return NoticeObserved(occurred_at=T0, scan_id=SCAN_ID, entity_id="2026/1", payload=snapshot)


def message(event: NoticeEvent) -> bytes:
    return serialize_event(event)


def stored_deleted_at(engine: Engine) -> datetime | None:
    with engine.connect() as connection:
        deleted_at: datetime | None = connection.execute(
            select(Notice.deleted_at).where(Notice.entity_id == "2026/1")
        ).scalar_one()
    return deleted_at


def test_events_are_committed_one_message_at_a_time(committing_engine: Engine) -> None:
    handler = EventHandler(committing_engine.begin)
    removed = NoticeRemoved(occurred_at=T0, scan_id=SCAN_ID, entity_id="2026/1")
    seen = NoticeSeen(occurred_at=T0.replace(hour=13), scan_id=SCAN_ID, entity_id="2026/1")

    with capture_logs() as logs:
        handler.handle(message(observed()))
        handler.handle(message(removed))
        removed_at = stored_deleted_at(committing_engine)
        handler.handle(message(seen))

    assert removed_at == T0
    assert stored_deleted_at(committing_engine) is None
    assert [log["event"] for log in logs] == ["notice_written", "notice_removed", "notice_returned"]


def test_dropped_values_are_logged_by_code_without_values(committing_engine: Engine) -> None:
    with capture_logs() as logs:
        EventHandler(committing_engine.begin).handle(message(observed(height=7.5)))

    warning = next(log for log in logs if log["event"] == "notice_values_dropped")
    assert warning["entity_id"] == "2026/1"
    assert warning["issues"] == ("height_out_of_range",)
    assert "7.5" not in str(logs)


def test_an_unchanged_notice_is_not_logged_as_written(committing_engine: Engine) -> None:
    handler = EventHandler(committing_engine.begin)
    handler.handle(message(observed()))

    with capture_logs() as logs:
        handler.handle(message(observed()))

    assert logs == []


@pytest.mark.parametrize(
    ("value", "detail"),
    [
        (b"not json", "json_invalid"),
        (
            b'{"event_type": "notice.seen", "entity_id": "x"}',
            "notice.seen.entity_id,notice.seen.occurred_at,notice.seen.scan_id",
        ),
    ],
)
def test_an_invalid_message_is_a_dead_letter_without_values(
    committing_engine: Engine, value: bytes, detail: str
) -> None:
    with pytest.raises(DeadLetterError) as excinfo:
        EventHandler(committing_engine.begin).handle(value)

    assert excinfo.value.error_type == "invalid_message"
    assert excinfo.value.detail == detail


def test_a_rejected_notice_is_a_dead_letter(committing_engine: Engine) -> None:
    event = observed(arrest_warrants=[{"issuing_country_id": "N1", "charge": "x"}])

    with pytest.raises(DeadLetterError) as excinfo:
        EventHandler(committing_engine.begin).handle(message(event))

    assert (excinfo.value.error_type, excinfo.value.detail) == (
        "notice_rejected",
        "arrest_warrant_country_invalid",
    )


def test_data_the_database_refuses_is_a_dead_letter_and_rolled_back(
    committing_engine: Engine,
) -> None:
    # PostgreSQL text cannot contain a NUL character.
    event = observed(name="TES\x00TER")

    with pytest.raises(DeadLetterError) as excinfo:
        EventHandler(committing_engine.begin).handle(message(event))

    assert excinfo.value.error_type == "database_rejected"
    with committing_engine.connect() as connection:
        assert connection.execute(select(Notice.id)).first() is None


def refuse_connection() -> NoReturn:
    raise psycopg.OperationalError("connection refused")


def test_an_unreachable_database_is_transient() -> None:
    # The driver's connection error is raised directly: a real connection attempt to a
    # closed port can hang for a long time on Windows.
    unreachable = create_engine("postgresql+psycopg://", creator=refuse_connection)

    with pytest.raises(TransientError):
        EventHandler(unreachable.begin).handle(message(observed()))
