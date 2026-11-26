"""Web interface: notices, photos and possible face matches (ADR 0013)."""

import uvicorn
from sqlalchemy import create_engine

from interpol_observability import configure_logging, get_logger
from interpol_web.app import create_app
from interpol_web.config import WebSettings
from interpol_web.photos import S3PhotoReader
from interpol_web.queries import NoticeQueries

SERVICE_NAME = "web"
# How long a page waits for the database to answer a new connection.
CONNECT_TIMEOUT_SECONDS = 5


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
    # Connects as the read-only database user (ADR 0013); pre_ping replaces connections
    # the database closed while the page was idle. Without a connect timeout a page would
    # wait forever while the database is down; now it fails after a few seconds.
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
    photos = S3PhotoReader.connect(
        settings.storage_endpoint_url,
        settings.storage_access_key.get_secret_value(),
        settings.storage_secret_key.get_secret_value(),
        settings.storage_bucket,
    )
    try:
        uvicorn.run(
            create_app(settings, NoticeQueries(engine.connect), photos),
            host=settings.host,
            port=settings.port,
            # Keep our JSON logging instead of uvicorn's own format.
            log_config=None,
            # The access log would write every URL, and a search URL can hold a name.
            access_log=False,
        )
    finally:
        engine.dispose()
    logger.info("web_stopped")
