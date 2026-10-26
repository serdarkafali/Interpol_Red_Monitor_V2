"""Sends notice events to Kafka, or only counts them during a dry run (ADR 0005).

``flush`` is the commit point: it returns only when every event published so far was
acknowledged by Kafka, and raises otherwise. Callers save their state only after a
successful flush, so a crash re-sends events instead of losing them (at-least-once).
"""

from collections import Counter
from collections.abc import Callable
from typing import Any, Protocol, Self

from confluent_kafka import KafkaError, Producer

from interpol_contracts import NOTICES_TOPIC, NoticeEvent, event_key, serialize_event
from interpol_observability import get_logger, kafka_client_logger

PRODUCER_SETTINGS: dict[str, str | bool] = {
    "acks": "all",
    "enable.idempotence": True,
    # Java clients hash keys with murmur2; librdkafka's default (CRC32) would put the
    # same entity_id on a different partition than a Java producer would.
    "partitioner": "murmur2_random",
}

logger = get_logger(__name__)

DeliveryReport = Callable[[KafkaError | None, Any], None]


class PublishError(Exception):
    """Some events were not acknowledged by Kafka."""


class EventPublisher(Protocol):
    """Where the collector sends its events."""

    def publish(self, event: NoticeEvent) -> None: ...

    def flush(self) -> None: ...


class KafkaProducer(Protocol):
    """The part of confluent_kafka.Producer the publisher uses."""

    def produce(
        self, topic: str, value: bytes, key: bytes, *, on_delivery: DeliveryReport
    ) -> None: ...

    def poll(self, timeout: float) -> int: ...

    def flush(self, timeout: float) -> int: ...


class KafkaEventPublisher:
    """Publishes events to the notices topic, keyed by entity id."""

    def __init__(
        self,
        producer: KafkaProducer,
        *,
        topic: str = NOTICES_TOPIC,
        flush_timeout_seconds: float = 60.0,
    ) -> None:
        self._producer = producer
        self._topic = topic
        self._flush_timeout_seconds = flush_timeout_seconds
        self._failed_deliveries = 0

    @classmethod
    def connect(cls, bootstrap_servers: str, *, client_id: str = "collector") -> Self:
        """Create a publisher backed by a real Kafka producer."""
        settings = {
            "bootstrap.servers": bootstrap_servers,
            "client.id": client_id,
            # librdkafka's own logs in our JSON format instead of plain text on stderr.
            "logger": kafka_client_logger(),
        }
        return cls(Producer({**settings, **PRODUCER_SETTINGS}))

    def publish(self, event: NoticeEvent) -> None:
        """Queue one event; delivery is confirmed by ``flush``."""
        value = serialize_event(event)
        key = event_key(event)
        try:
            self._producer.produce(self._topic, value, key, on_delivery=self._report)
        except BufferError:
            # The local queue is full: let it drain, then try once more.
            self._producer.poll(1.0)
            self._producer.produce(self._topic, value, key, on_delivery=self._report)
        self._producer.poll(0)

    def flush(self) -> None:
        """Wait until every queued event is acknowledged; raise if any was not."""
        remaining = self._producer.flush(self._flush_timeout_seconds)
        failed, self._failed_deliveries = self._failed_deliveries, 0
        if remaining or failed:
            raise PublishError(f"{remaining} events undelivered, {failed} deliveries failed")

    def _report(self, error: KafkaError | None, _message: Any) -> None:
        if error is not None:
            self._failed_deliveries += 1
            logger.error("kafka_delivery_failed", error=str(error))


class DryRunPublisher:
    """Counts events instead of sending them, for trial runs against the real API."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def publish(self, event: NoticeEvent) -> None:
        """Count the event by type; nothing leaves the process."""
        self._counts[event.event_type] += 1

    def flush(self) -> None:
        """Log how many events of each type would have been sent."""
        if self._counts:
            logger.info("dry_run_events", counts=dict(self._counts))
            self._counts.clear()
