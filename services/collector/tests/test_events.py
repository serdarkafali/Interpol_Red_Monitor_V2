"""Tests of the mapping from scans and details to notice events."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from interpol_collector.events import observed_event, removed_events, seen_events
from interpol_contracts import compute_content_hash, parse_event, serialize_event

SCAN_ID = uuid4()
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
DETAIL: dict[str, Any] = {"name": "DOE", "forename": "JANE", "height": 0, "new_field": 1}


def test_seen_events_are_sorted_and_carry_the_scan() -> None:
    events = seen_events(SCAN_ID, {"2026/2", "2026/1"}, NOW)

    assert [event.entity_id for event in events] == ["2026/1", "2026/2"]
    assert all(event.scan_id == SCAN_ID and event.occurred_at == NOW for event in events)
    assert all(event.event_type == "notice.seen" for event in events)


def test_removed_events_have_the_removed_type() -> None:
    events = removed_events(SCAN_ID, {"2026/3"}, NOW)

    assert [(event.event_type, event.entity_id) for event in events] == [
        ("notice.removed", "2026/3")
    ]


def test_observed_event_keeps_the_raw_detail_and_its_hash() -> None:
    event = observed_event(SCAN_ID, "2026/1", DETAIL, ["101", "100"], NOW)

    assert event.payload.content_hash == compute_content_hash(DETAIL, ["101", "100"])
    assert event.payload.detail.model_dump()["new_field"] == 1
    assert event.payload.picture_ids == ["101", "100"]


def test_observed_event_survives_kafka_serialization() -> None:
    event = observed_event(SCAN_ID, "2026/1", DETAIL, [], NOW)

    assert parse_event(serialize_event(event)) == event


def test_detail_without_a_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        observed_event(SCAN_ID, "2026/1", {"forename": "JANE"}, [], NOW)


def test_malformed_entity_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        seen_events(SCAN_ID, {"not-an-id"}, NOW)
