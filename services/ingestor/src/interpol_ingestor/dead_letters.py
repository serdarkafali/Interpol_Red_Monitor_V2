"""Writes messages that cannot be stored to the ingestor's dead letter topic (ADR 0008).

The dead letter message keeps the original key and bytes, so it can be inspected and
replayed later. Headers say why it failed and where it came from. ``send`` returns only
after Kafka acknowledged the message; the caller stores the source offset after that, so
a crash can never skip a message without it reaching the dead letter topic.
"""

from collections.abc import Callable
from typing import Any, Protocol, Self

from confluent_kafka import KafkaError, Message, Producer

from interpol_ingestor.handler import DeadLetterError

DEAD_LETTER_TOPIC = "ingestor.dlq"

PRODUCER_SETTINGS: dict[str, str | bool] = {"acks": "all", "enable.idempotence": True}

DeliveryReport = Callable[[KafkaError | None, Any], None]
Headers = list[tuple[str, str | bytes | None]]


class DeadLetterSendError(Exception):
    """Kafka did not acknowledge the dead letter message."""


class DeadLetterProducer(Protocol):
    """The part of confluent_kafka.Producer the dead letter writer uses."""

    def produce(
        self,
        topic: str,
        value: bytes | None,
        key: bytes | None,
        *,
        headers: Headers,
        on_delivery: DeliveryReport,
    ) -> None: ...

    def flush(self, timeout: float) -> int: ...


class DeadLetterWriter:
    """Sends one failed message at a time and waits for Kafka's acknowledgement."""

    def __init__(
        self,
        producer: DeadLetterProducer,
        *,
        topic: str = DEAD_LETTER_TOPIC,
        flush_timeout_seconds: float = 30.0,
    ) -> None:
        self._producer = producer
        self._topic = topic
        self._flush_timeout_seconds = flush_timeout_seconds
        self._errors: list[KafkaError] = []

    @classmethod
    def connect(cls, bootstrap_servers: str, *, client_id: str = "ingestor") -> Self:
        """Create a writer backed by a real Kafka producer."""
        settings = {"bootstrap.servers": bootstrap_servers, "client.id": client_id}
        return cls(Producer({**settings, **PRODUCER_SETTINGS}))

    def send(self, message: Message, error: DeadLetterError) -> None:
        """Write ``message`` with the reason of ``error``; raise if it was not delivered."""
        self._producer.produce(
            self._topic,
            message.value(),
            message.key(),
            headers=dead_letter_headers(message, error),
            on_delivery=self._report,
        )
        remaining = self._producer.flush(self._flush_timeout_seconds)
        errors, self._errors = self._errors, []
        if remaining or errors:
            raise DeadLetterSendError(f"{remaining} undelivered, {len(errors)} failed")

    def _report(self, error: KafkaError | None, _message: Any) -> None:
        if error is not None:
            self._errors.append(error)


def dead_letter_headers(message: Message, error: DeadLetterError) -> Headers:
    """Why the message failed and where it came from; never any value from the message."""
    return [
        ("error_type", error.error_type),
        ("error_detail", error.detail),
        ("source_topic", message.topic()),
        ("source_partition", str(message.partition())),
        ("source_offset", str(message.offset())),
    ]
