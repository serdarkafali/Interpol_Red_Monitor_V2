"""Write made-up notice events to the local Kafka, to try the ingestor end to end.

No Interpol data is involved: every person and value below is invented, and every entity
id uses the year 1900, which no real notice has. The events cover each path of the
ingestor (ADR 0008):

- 1900/1 observed twice with the same content (the second is skipped), then seen
- 1900/2 observed with an out-of-range weight (stored as NULL, logged as an issue)
- 1900/3 observed, then removed (soft deleted)
- 1900/4 a value that is not JSON (goes to ingestor.dlq)
- 1900/5 observed with a broken arrest warrant (rejected, goes to ingestor.dlq)
- 1900/6 observed with a country code the countries table does not know (added)
- 1900/99 seen without ever being observed (ignored)

Run from the repository root while Kafka is up:
    uv run --env-file .env python scripts/produce_fake_events.py

Remove the made-up notices afterwards with:
    DELETE FROM notices WHERE entity_id LIKE '1900/%';
"""

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from confluent_kafka import Producer

from interpol_contracts import (
    NOTICES_TOPIC,
    NoticeEvent,
    NoticeObserved,
    NoticeRemoved,
    NoticeSeen,
    NoticeSnapshot,
    compute_content_hash,
    serialize_event,
)

SCAN_ID = uuid4()


def detail(**changes: Any) -> dict[str, Any]:
    """A made-up notice detail in Interpol's raw shape."""
    return {
        "name": "TESTPERSON",
        "forename": "FAKE",
        "sex_id": "M",
        "date_of_birth": "1970/01/01",
        "country_of_birth_id": "DE",
        "nationalities": ["DE"],
        "height": 1.75,
        "weight": 70,
        "arrest_warrants": [{"issuing_country_id": "NL", "charge": "Invented charge for testing"}],
        **changes,
    }


def observed(entity_id: str, raw: dict[str, Any]) -> NoticeObserved:
    pictures = [f"{entity_id.replace('/', '-')}-photo"]
    snapshot = NoticeSnapshot.model_validate(
        {
            "detail": raw,
            "picture_ids": pictures,
            "content_hash": compute_content_hash(raw, pictures),
        }
    )
    return NoticeObserved(
        occurred_at=datetime.now(UTC), scan_id=SCAN_ID, entity_id=entity_id, payload=snapshot
    )


def seen(entity_id: str) -> NoticeSeen:
    return NoticeSeen(occurred_at=datetime.now(UTC), scan_id=SCAN_ID, entity_id=entity_id)


def removed(entity_id: str) -> NoticeRemoved:
    return NoticeRemoved(occurred_at=datetime.now(UTC), scan_id=SCAN_ID, entity_id=entity_id)


def main() -> None:
    servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    # Same partitioner as the collector, so a key lands on the same partition.
    producer = Producer(
        {"bootstrap.servers": servers, "acks": "all", "partitioner": "murmur2_random"}
    )
    events: list[NoticeEvent] = [
        observed("1900/1", detail()),
        observed("1900/1", detail()),
        seen("1900/1"),
        observed("1900/2", detail(weight=900)),
        observed("1900/3", detail()),
        removed("1900/3"),
        observed("1900/5", detail(arrest_warrants=[{"issuing_country_id": "N1", "charge": "x"}])),
        observed("1900/6", detail(nationalities=["DE", "QZ"])),
        seen("1900/99"),
    ]
    for event in events:
        producer.produce(NOTICES_TOPIC, serialize_event(event), event.entity_id.encode())
    producer.produce(NOTICES_TOPIC, b"not json", b"1900/4")

    remaining = producer.flush(30)
    if remaining:
        raise SystemExit(f"{remaining} messages were not delivered; is Kafka running?")
    print(f"{len(events) + 1} made-up messages written to {NOTICES_TOPIC} on {servers}")


if __name__ == "__main__":
    main()
