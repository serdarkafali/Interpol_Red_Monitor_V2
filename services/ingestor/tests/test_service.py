"""Tests of the consumer loop: offsets, dead letters and retries."""

from collections import deque

import pytest
from confluent_kafka import KafkaError, KafkaException, Message, TopicPartition

from interpol_ingestor.dead_letters import DeadLetterSendError
from interpol_ingestor.handler import DeadLetterError, TransientError
from interpol_ingestor.service import IngestorService


def kafka_message(offset: int, value: bytes = b"event") -> Message:
    return Message(
        topic="interpol.notices.v1", partition=0, offset=offset, key=b"2026/1", value=value
    )


class FakeConsumer:
    """Serves queued messages; a seek puts the message at that offset back in front."""

    def __init__(self, *messages: Message) -> None:
        self.queue = deque(messages)
        self.delivered: dict[int, Message] = {}
        self.stored: list[int | None] = []
        self.seeks: list[int] = []
        self.closed = False

    def poll(self, timeout: float) -> Message | None:
        if not self.queue:
            return None
        message = self.queue.popleft()
        offset = message.offset()
        if offset is not None:
            self.delivered[offset] = message
        return message

    def store_offsets(self, message: Message) -> None:
        self.stored.append(message.offset())

    def seek(self, partition: TopicPartition) -> None:
        self.seeks.append(partition.offset)
        self.queue.appendleft(self.delivered[partition.offset])

    def close(self) -> None:
        self.closed = True


class FakeHandler:
    """Raises the queued exceptions in order, then succeeds."""

    def __init__(self, *failures: Exception) -> None:
        self.failures = deque(failures)
        self.handled: list[bytes] = []

    def handle(self, value: bytes) -> None:
        self.handled.append(value)
        if self.failures:
            raise self.failures.popleft()


class FakeDeadLetters:
    def __init__(self, *failures: Exception) -> None:
        self.failures = deque(failures)
        self.sent: list[tuple[int | None, str]] = []

    def send(self, message: Message, error: DeadLetterError) -> None:
        if self.failures:
            raise self.failures.popleft()
        self.sent.append((message.offset(), error.error_type))


def service(
    consumer: FakeConsumer,
    handler: FakeHandler,
    dead_letters: FakeDeadLetters | None = None,
    sleeps: list[float] | None = None,
) -> IngestorService:
    recorded = sleeps if sleeps is not None else []
    return IngestorService(
        consumer=consumer,
        handler=handler,
        dead_letters=dead_letters or FakeDeadLetters(),
        sleep=recorded.append,
    )


def test_offsets_are_stored_after_each_handled_message() -> None:
    consumer = FakeConsumer(kafka_message(0), kafka_message(1))

    loop = service(consumer, FakeHandler())
    loop.step()
    loop.step()
    loop.step()  # nothing to poll

    assert consumer.stored == [0, 1]


def test_a_dead_letter_is_sent_before_its_offset_is_stored() -> None:
    consumer = FakeConsumer(kafka_message(0))
    dead_letters = FakeDeadLetters()

    service(consumer, FakeHandler(DeadLetterError("invalid_message")), dead_letters).step()

    assert dead_letters.sent == [(0, "invalid_message")]
    assert consumer.stored == [0]


def test_a_transient_error_retries_the_same_message_with_growing_delays() -> None:
    consumer = FakeConsumer(kafka_message(0), kafka_message(1))
    handler = FakeHandler(TransientError(), TransientError())
    sleeps: list[float] = []

    loop = service(consumer, handler, sleeps=sleeps)
    for _ in range(4):
        loop.step()

    assert handler.handled == [b"event"] * 4
    assert consumer.seeks == [0, 0]
    assert consumer.stored == [0, 1]
    assert sleeps == [5.0, 10.0]


def test_the_retry_delay_is_capped_and_reset_after_a_success() -> None:
    consumer = FakeConsumer(kafka_message(0), kafka_message(1))
    # Message 0 fails six times, then succeeds; message 1 then fails once.
    handler = FakeHandler(*[TransientError() for _ in range(6)])
    sleeps: list[float] = []

    loop = service(consumer, handler, sleeps=sleeps)
    for _ in range(7):
        loop.step()
    handler.failures.append(TransientError())
    loop.step()

    assert sleeps == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 5.0]
    assert consumer.stored == [0]


def test_a_failed_dead_letter_write_is_retried_not_skipped() -> None:
    consumer = FakeConsumer(kafka_message(0))
    dead_letters = FakeDeadLetters(DeadLetterSendError("down"))
    handler = FakeHandler(DeadLetterError("invalid_message"), DeadLetterError("invalid_message"))

    loop = service(consumer, handler, dead_letters)
    loop.step()
    loop.step()

    assert consumer.seeks == [0]
    assert dead_letters.sent == [(0, "invalid_message")]
    assert consumer.stored == [0]


def test_an_unexpected_error_stops_the_loop_without_storing_and_closes() -> None:
    consumer = FakeConsumer(kafka_message(0))

    with pytest.raises(ZeroDivisionError):
        service(consumer, FakeHandler(ZeroDivisionError())).run(should_stop=lambda: False)

    assert consumer.stored == []
    assert consumer.closed


def test_run_stops_when_asked_and_closes_the_consumer() -> None:
    consumer = FakeConsumer(kafka_message(0), kafka_message(1))
    steps = iter([False, True])

    service(consumer, FakeHandler()).run(should_stop=lambda: next(steps))

    assert consumer.stored == [0]
    assert consumer.closed


def test_a_non_fatal_kafka_error_is_skipped_and_a_fatal_one_raises() -> None:
    transient = Message(error=KafkaError(KafkaError._TRANSPORT))
    fatal = Message(error=KafkaError(KafkaError._FATAL, fatal=True))
    consumer = FakeConsumer(transient, fatal)

    loop = service(consumer, FakeHandler())
    loop.step()
    with pytest.raises(KafkaException):
        loop.step()

    assert consumer.stored == []
