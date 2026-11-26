"""Web interface: notices, photos and possible face matches (ADR 0013)."""

import uvicorn

from interpol_observability import configure_logging, get_logger
from interpol_web.app import create_app
from interpol_web.config import WebSettings

SERVICE_NAME = "web"


def main() -> None:
    """Entry point of the web interface."""
    settings = WebSettings()
    configure_logging(
        service_name=SERVICE_NAME,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    logger = get_logger(__name__)
    # The database URL and the password are secrets and are not logged.
    logger.info("web_starting", host=settings.host, port=settings.port)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        # Keep our JSON logging instead of uvicorn's own format.
        log_config=None,
        # The access log would write every URL, and a search URL can hold a name.
        access_log=False,
    )
    logger.info("web_stopped")
