"""Collector service: reads the Interpol red notice list and publishes events to Kafka."""

import signal
import threading
from datetime import timedelta
from types import FrameType

from interpol_collector.config import CollectorSettings
from interpol_collector.curl_transport import CurlTransport
from interpol_collector.detail_schedule import DetailScheduleFile
from interpol_collector.interpol_client import InterpolClient
from interpol_collector.list_scanner import ListScanner, iso_country_codes
from interpol_collector.publishers import DryRunPublisher, EventPublisher, KafkaEventPublisher
from interpol_collector.removal_tracker import StateFile
from interpol_collector.service import CollectorService, Timing
from interpol_observability import configure_logging, get_logger

SERVICE_NAME = "collector"


def main() -> None:
    """Entry point of the collector service."""
    settings = CollectorSettings()
    configure_logging(
        service_name=SERVICE_NAME,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    logger = get_logger(__name__)
    logger.info(
        "collector_starting",
        api_base_url=str(settings.interpol_api_base_url),
        dry_run=settings.dry_run,
        state_dir=str(settings.state_dir.resolve()),
    )

    stop = threading.Event()

    def request_stop(signal_number: int, _frame: FrameType | None) -> None:
        logger.info("collector_stopping", signal=signal.Signals(signal_number).name)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    client = InterpolClient(
        CurlTransport(),
        str(settings.interpol_api_base_url),
        delay_range=(settings.request_delay_min_seconds, settings.request_delay_max_seconds),
    )
    publisher: EventPublisher = (
        DryRunPublisher()
        if settings.dry_run
        else KafkaEventPublisher.connect(settings.kafka_bootstrap_servers)
    )
    service = CollectorService(
        scanner=ListScanner(client, iso_country_codes(), should_stop=stop.is_set),
        details=client,
        publisher=publisher,
        removal_file=StateFile(settings.state_dir / "removal_state.json"),
        schedule_file=DetailScheduleFile(settings.state_dir / "detail_schedule.json"),
        timing=Timing(
            list_scan_interval=timedelta(hours=settings.list_scan_interval_hours),
            detail_refresh=timedelta(hours=settings.detail_refresh_hours),
        ),
        max_removed_fraction=settings.max_removed_fraction,
        sleep=stop.wait,
    )
    service.run(should_stop=stop.is_set)
    logger.info("collector_stopped")
