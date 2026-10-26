"""Ingestor service: reads notice events from Kafka and writes them to PostgreSQL."""

import signal
import threading
from types import FrameType

from confluent_kafka import Consumer
from sqlalchemy import create_engine

from interpol_contracts import NOTICES_TOPIC
from interpol_ingestor.config import IngestorSettings
from interpol_ingestor.dead_letters import DeadLetterWriter
from interpol_ingestor.handler import EventHandler
from interpol_ingestor.service import CONSUMER_SETTINGS, IngestorService
from interpol_observability import configure_logging, get_logger, kafka_client_logger

SERVICE_NAME = "ingestor"


def main() -> None:
    """Entry point of the ingestor service."""
    settings = IngestorSettings()
    configure_logging(
        service_name=SERVICE_NAME,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    logger = get_logger(__name__)
    # The database URL is not logged: it contains the password.
    logger.info(
        "ingestor_starting",
        kafka_bootstrap_servers=settings.kafka_bootstrap_servers,
        consumer_group=settings.consumer_group,
        ignore_content_hash=settings.ignore_content_hash,
    )

    stop = threading.Event()

    def request_stop(signal_number: int, _frame: FrameType | None) -> None:
        logger.info("ingestor_stopping", signal=signal.Signals(signal_number).name)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    # pool_pre_ping replaces connections the database closed while the service waited.
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.consumer_group,
            "client.id": SERVICE_NAME,
            # librdkafka's own logs in our JSON format instead of plain text on stderr.
            "logger": kafka_client_logger(),
            **CONSUMER_SETTINGS,
        }
    )
    consumer.subscribe([NOTICES_TOPIC])
    service = IngestorService(
        consumer=consumer,
        handler=EventHandler(engine.begin, ignore_content_hash=settings.ignore_content_hash),
        dead_letters=DeadLetterWriter.connect(settings.kafka_bootstrap_servers),
        # A retry wait ends at once when a stop is requested.
        sleep=stop.wait,
    )
    try:
        service.run(should_stop=stop.is_set)
    except Exception:
        # A programming error (ADR 0008): log it and exit non-zero so it is noticed.
        logger.exception("ingestor_crashed")
        raise
    finally:
        engine.dispose()
    logger.info("ingestor_stopped")
