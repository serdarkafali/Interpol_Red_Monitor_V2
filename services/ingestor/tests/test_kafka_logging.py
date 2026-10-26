"""librdkafka's own logs must come out in the service's JSON format (Faz 7, ELK)."""

import json
import logging
from collections.abc import Iterator

import pytest
import structlog
from confluent_kafka import Producer

from interpol_observability import configure_logging, kafka_client_logger


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def test_librdkafka_logs_are_rendered_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service_name="ingestor")
    # No bootstrap.servers: librdkafka logs a configuration warning without any network.
    producer = Producer({"logger": kafka_client_logger()})

    producer.poll(0)

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert any(
        record["logger"] == "librdkafka" and "bootstrap.servers" in str(record["event"])
        for record in records
    )
