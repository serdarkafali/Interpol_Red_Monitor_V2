"""Tests of the notice event contract. All data here is made up."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from interpol_contracts import (
    NoticeDetail,
    NoticeObserved,
    NoticeRemoved,
    NoticeSeen,
    NoticeSnapshot,
    compute_content_hash,
    event_key,
    parse_event,
    serialize_event,
)

RAW_DETAIL: dict[str, Any] = {
    "name": "DOE",
    "forename": "JOHN",
    "sex_id": "M",
    "date_of_birth": "1980/01/31",
    "nationalities": ["TR", "DE"],
    "height": 0,
    "arrest_warrants": [{"charge": "Example charge", "issuing_country_id": "US"}],
    "field_added_by_interpol_later": {"nested": True},
}


def make_observed(**overrides: Any) -> NoticeObserved:
    """Build a valid ``notice.observed`` event from made-up data."""
    picture_ids = ["101", "100"]
    values: dict[str, Any] = {
        "occurred_at": datetime(2026, 10, 20, 9, 30, tzinfo=UTC),
        "scan_id": uuid4(),
        "entity_id": "2026/12345",
        "payload": NoticeSnapshot(
            detail=NoticeDetail.model_validate(RAW_DETAIL),
            picture_ids=picture_ids,
            thumbnail_picture_id="102",
            content_hash=compute_content_hash(RAW_DETAIL, picture_ids),
        ),
        **overrides,
    }
    return NoticeObserved(**values)


def test_observed_event_survives_a_round_trip() -> None:
    event = make_observed()

    parsed = parse_event(serialize_event(event))

    assert isinstance(parsed, NoticeObserved)
    assert parsed == event


def test_removed_event_survives_a_round_trip() -> None:
    event = NoticeRemoved(
        occurred_at=datetime(2026, 10, 20, tzinfo=UTC), scan_id=uuid4(), entity_id="2026/1"
    )

    parsed = parse_event(serialize_event(event))

    assert isinstance(parsed, NoticeRemoved)
    assert parsed == event


def test_seen_event_survives_a_round_trip() -> None:
    event = NoticeSeen(
        occurred_at=datetime(2026, 10, 21, tzinfo=UTC), scan_id=uuid4(), entity_id="2026/7"
    )

    parsed = parse_event(serialize_event(event))

    assert isinstance(parsed, NoticeSeen)
    assert parsed == event
    assert event_key(parsed) == b"2026/7"


def test_raw_values_and_unknown_interpol_fields_are_kept() -> None:
    parsed = parse_event(serialize_event(make_observed()))

    assert isinstance(parsed, NoticeObserved)
    detail = parsed.payload.detail
    # The contract carries the source as received: 0 stays 0, the date stays a string.
    assert detail.height == 0
    assert detail.date_of_birth == "1980/01/31"
    assert detail.model_extra == {"field_added_by_interpol_later": {"nested": True}}


def test_unknown_envelope_fields_are_ignored() -> None:
    document = json.loads(serialize_event(make_observed()))
    document["field_from_a_newer_producer"] = "ignored"

    parsed = parse_event(json.dumps(document).encode())

    assert isinstance(parsed, NoticeObserved)


def test_unknown_event_type_is_rejected() -> None:
    document = json.loads(serialize_event(make_observed()))
    document["event_type"] = "notice.exploded"

    with pytest.raises(ValidationError):
        parse_event(json.dumps(document).encode())


def test_timestamp_without_time_zone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_observed(occurred_at=datetime(2026, 10, 20, 9, 30))


@pytest.mark.parametrize("entity_id", ["2026-12345", "12345", "2026/", "", "x2026/1", "2026/1x"])
def test_malformed_entity_id_is_rejected(entity_id: str) -> None:
    with pytest.raises(ValidationError):
        make_observed(entity_id=entity_id)


def test_content_hash_ignores_key_and_photo_order() -> None:
    reordered = dict(reversed(list(RAW_DETAIL.items())))

    assert compute_content_hash(RAW_DETAIL, ["100", "101"]) == compute_content_hash(
        reordered, ["101", "100"]
    )


def test_content_hash_changes_when_the_content_changes() -> None:
    changed = {**RAW_DETAIL, "forename": "JANE"}

    assert compute_content_hash(RAW_DETAIL, ["100"]) != compute_content_hash(changed, ["100"])
    assert compute_content_hash(RAW_DETAIL, ["100"]) != compute_content_hash(RAW_DETAIL, ["999"])


def test_event_key_is_the_entity_id() -> None:
    assert event_key(make_observed()) == b"2026/12345"


def test_events_cannot_be_modified() -> None:
    event = make_observed()

    with pytest.raises(ValidationError):
        event.entity_id = "2026/99999"
