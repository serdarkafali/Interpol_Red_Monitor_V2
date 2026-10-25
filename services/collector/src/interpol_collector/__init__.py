"""Collector service: fetches red notices from the Interpol API."""

from interpol_collector.config import CollectorSettings
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
    logger.info("collector_starting", api_base_url=str(settings.interpol_api_base_url))
