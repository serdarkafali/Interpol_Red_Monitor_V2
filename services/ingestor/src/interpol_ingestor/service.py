"""The ingestor's main loop: one message, one transaction, then its offset (ADR 0008).

The offset of a message is stored only after its transaction was committed or it was
written to the dead letter topic. A crash in between makes Kafka deliver the message
again, which is harmless because every write is idempotent (at-least-once).

When the database or the dead letter topic cannot be reached, the loop waits and seeks
back to the same message instead of skipping it. The wait stays below Kafka's
``max.poll.interval.ms`` (5 minutes by default), so the consumer keeps its partitions.
"""

import time
from collections.abc import Callable
from typing import Protocol

from confluent_kafka import KafkaException, Message, TopicPartition

from interpol_ingestor.dead_letters import DeadLetterSendError
from interpol_ingestor.handler import DeadLetterError, TransientError
from interpol_observability import get_logger

logger = get_logger(__name__)

# At-least-once (ADR 0005, ADR 0008): offsets are stored by the loop after a message was
# handled and committed in the background; without a committed offset (first start) the
# topic is read from the beginning instead of skipping what was written before.
CONSUMER_SETTINGS: dict[str, str | bool] = {
    "enable.auto.offset.store": False,
    "enable.auto.commit": True,
    "auto.offset.reset": "earliest",
}

FIRST_RETRY_DELAY_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 60.0


class KafkaConsumer(Protocol):
    """The part of confluent_kafka.Consumer the loop uses."""

    def poll(self, timeout: float) -> Message | None: ...

    def store_offsets(self, message: Message) -> None: ...

    def seek(self, partition: TopicPartition) -> None: ...

    def close(self) -> None: ...


class Handler(Protocol):
    """Writes one message value; the EventHandler fits."""

    def handle(self, value: bytes) -> None: ...


class DeadLetters(Protocol):
    """Where unstorable messages go; the DeadLetterWriter fits."""

    def send(self, message: Message, error: DeadLetterError) -> None: ...

    def poll(self) -> None: ...


class IngestorService:
    """Consumes notice events until asked to stop."""

    def __init__(
        self,
        *,
        consumer: KafkaConsumer,
        handler: Handler,
        dead_letters: DeadLetters,
        sleep: Callable[[float], object] = time.sleep,
        poll_timeout_seconds: float = 1.0,
    ) -> None:
        self._consumer = consumer
        self._handler = handler
        self._dead_letters = dead_letters
        self._sleep = sleep
        self._poll_timeout_seconds = poll_timeout_seconds
        self._retry_delay = FIRST_RETRY_DELAY_SECONDS

    def run(self, should_stop: Callable[[], bool]) -> None:
        """Process messages until ``should_stop`` returns true, then close the consumer.

        Closing commits the offsets stored so far. An unexpected exception also closes
        the consumer and is re-raised; the failed message's offset was not stored.
        """
        try:
            while not should_stop():
                self.step()
        finally:
            self._consumer.close()

    def step(self) -> None:
        """Poll once and process the message, if any."""
        self._dead_letters.poll()
        message = self._consumer.poll(self._poll_timeout_seconds)
        if message is None:
            return
        error = message.error()
        if error is not None:
            if error.fatal():
                raise KafkaException(error)
            logger.warning("kafka_consume_error", error=str(error))
            return
        try:
            self._process(message)
        except (TransientError, DeadLetterSendError) as exc:
            self._retry_later(message, exc)
            return
        self._consumer.store_offsets(message)
        self._retry_delay = FIRST_RETRY_DELAY_SECONDS

    def _process(self, message: Message) -> None:
        try:
            self._handler.handle(message.value() or b"")
        except DeadLetterError as exc:
            self._dead_letters.send(message, exc)
            logger.error(
                "message_dead_lettered",
                entity_id=_entity_id(message),
                partition=message.partition(),
                offset=message.offset(),
                error_type=exc.error_type,
                error_detail=exc.detail,
            )

    def _retry_later(self, message: Message, exc: Exception) -> None:
        logger.warning(
            "message_retry_scheduled",
            entity_id=_entity_id(message),
            partition=message.partition(),
            offset=message.offset(),
            reason=type(exc).__name__,
            retry_in_seconds=self._retry_delay,
        )
        self._sleep(self._retry_delay)
        self._retry_delay = min(self._retry_delay * 2, MAX_RETRY_DELAY_SECONDS)
        topic, partition, offset = message.topic(), message.partition(), message.offset()
        if topic is None or partition is None or offset is None:
            raise RuntimeError("a consumed message always has a topic, partition and offset")
        # The next poll returns the same message again from this offset.
        self._consumer.seek(TopicPartition(topic, partition, offset))


def _entity_id(message: Message) -> str | None:
    """The message key is the entity id (ADR 0005); safe to log, unlike the value."""
    key = message.key()
    return key.decode("utf-8", errors="replace") if key is not None else None
