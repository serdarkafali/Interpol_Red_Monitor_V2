"""Tests of the Kafka and dry-run publishers. No test talks to a real broker."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from confluent_kafka import KafkaError
from structlog.testing import capture_logs

from interpol_collector.publishers import (
    PRODUCER_SETTINGS,
    DeliveryReport,
    DryRunPublisher,
    KafkaEventPublisher,
    PublishError,
)
from interpol_contracts import NOTICES_TOPIC, NoticeRemoved, NoticeSeen, parse_event

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def seen(entity_id: str = "2026/1") -> NoticeSeen:
    return NoticeSeen(scan_id=uuid4(), entity_id=entity_id, occurred_at=NOW)


class FakeProducer:
    """Records produced messages and reports their delivery when flushed."""

    def __init__(self, *, fail_deliveries: bool = False, undelivered: int = 0) -> None:
        self.messages: list[tuple[str, bytes, bytes]] = []
        self._pending: list[DeliveryReport] = []
        self._fail_deliveries = fail_deliveries
        self._undelivered = undelivered

    def produce(self, topic: str, value: bytes, key: bytes, *, on_delivery: DeliveryReport) -> None:
        self.messages.append((topic, value, key))
        self._pending.append(on_delivery)

    def poll(self, timeout: float) -> int:
        return 0

    def flush(self, timeout: float) -> int:
        error = KafkaError(KafkaError._MSG_TIMED_OUT) if self._fail_deliveries else None
        for report in self._pending:
            report(error, None)
        self._pending.clear()
        return self._undelivered


def test_event_is_sent_to_the_notices_topic_keyed_by_entity_id() -> None:
    producer = FakeProducer()
    publisher = KafkaEventPublisher(producer)
    event = seen("2026/7")

    publisher.publish(event)
    publisher.flush()

    [(topic, value, key)] = producer.messages
    assert topic == NOTICES_TOPIC
    assert key == b"2026/7"
    assert parse_event(value) == event


def test_failed_delivery_makes_flush_raise() -> None:
    publisher = KafkaEventPublisher(FakeProducer(fail_deliveries=True))
    publisher.publish(seen())

    with capture_logs() as logs, pytest.raises(PublishError, match="1 deliveries failed"):
        publisher.flush()

    assert any(log["event"] == "kafka_delivery_failed" for log in logs)


def test_undelivered_events_make_flush_raise() -> None:
    publisher = KafkaEventPublisher(FakeProducer(undelivered=2))
    publisher.publish(seen())

    with pytest.raises(PublishError, match="2 events undelivered"):
        publisher.flush()


def test_failures_are_counted_once() -> None:
    producer = FakeProducer(fail_deliveries=True)
    publisher = KafkaEventPublisher(producer)
    publisher.publish(seen())
    with pytest.raises(PublishError):
        publisher.flush()

    producer._fail_deliveries = False
    publisher.publish(seen())

    publisher.flush()


def test_producer_settings_follow_adr_0005() -> None:
    assert PRODUCER_SETTINGS["acks"] == "all"
    assert PRODUCER_SETTINGS["enable.idempotence"] is True
    assert PRODUCER_SETTINGS["partitioner"] == "murmur2_random"


def test_dry_run_only_counts_events() -> None:
    publisher = DryRunPublisher()
    publisher.publish(seen("2026/1"))
    publisher.publish(seen("2026/2"))
    publisher.publish(NoticeRemoved(scan_id=uuid4(), entity_id="2026/3", occurred_at=NOW))

    with capture_logs() as logs:
        publisher.flush()
        publisher.flush()

    counts: list[Any] = [log["counts"] for log in logs if log["event"] == "dry_run_events"]
    assert counts == [{"notice.seen": 2, "notice.removed": 1}]
