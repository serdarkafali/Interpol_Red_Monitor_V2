"""Tests of writing unstorable messages to the dead letter topic."""

from typing import Any

import pytest
from confluent_kafka import KafkaError, Message

from interpol_ingestor.dead_letters import (
    DeadLetterSendError,
    DeadLetterWriter,
    DeliveryReport,
    Headers,
)
from interpol_ingestor.handler import DeadLetterError

SOURCE = Message(
    topic="interpol.notices.v1", partition=2, offset=41, key=b"2026/1", value=b"raw bytes"
)


class FakeProducer:
    """Records produced messages; ``fail`` makes the delivery report an error."""

    def __init__(self, *, fail: bool = False, undelivered: int = 0) -> None:
        self.produced: list[dict[str, Any]] = []
        self._fail = fail
        self._undelivered = undelivered
        self._report: DeliveryReport | None = None

    def produce(
        self,
        topic: str,
        value: bytes | None,
        key: bytes | None,
        *,
        headers: Headers,
        on_delivery: DeliveryReport,
    ) -> None:
        self.produced.append({"topic": topic, "value": value, "key": key, "headers": headers})
        self._report = on_delivery

    def poll(self, timeout: float) -> int:
        return 0

    def flush(self, timeout: float) -> int:
        assert self._report is not None
        self._report(KafkaError(KafkaError._MSG_TIMED_OUT) if self._fail else None, None)
        return self._undelivered


def test_the_original_message_is_sent_with_its_failure_reason() -> None:
    producer = FakeProducer()

    DeadLetterWriter(producer).send(SOURCE, DeadLetterError("notice_rejected", "reason_code"))

    [sent] = producer.produced
    assert (sent["topic"], sent["key"], sent["value"]) == ("ingestor.dlq", b"2026/1", b"raw bytes")
    assert sent["headers"] == [
        ("error_type", "notice_rejected"),
        ("error_detail", "reason_code"),
        ("source_topic", "interpol.notices.v1"),
        ("source_partition", "2"),
        ("source_offset", "41"),
    ]


@pytest.mark.parametrize("producer", [FakeProducer(fail=True), FakeProducer(undelivered=1)])
def test_an_unacknowledged_dead_letter_raises(producer: FakeProducer) -> None:
    with pytest.raises(DeadLetterSendError):
        DeadLetterWriter(producer).send(SOURCE, DeadLetterError("invalid_message"))
