"""Face worker: downloads notice photos and finds their faces (ADR 0011, ADR 0012)."""

import signal
import threading
from types import FrameType

from sqlalchemy import create_engine

from interpol_face_worker.config import FaceWorkerSettings
from interpol_face_worker.curl_transport import CurlTransport
from interpol_face_worker.face_analysis import FaceAnalyzer
from interpol_face_worker.interpol_photos import PhotoClient
from interpol_face_worker.photo_store import S3PhotoStore
from interpol_face_worker.repository import PhotoRepository
from interpol_face_worker.service import FaceWorkerService
from interpol_observability import configure_logging, get_logger

SERVICE_NAME = "face-worker"


def main() -> None:
    """Entry point of the face worker service."""
    settings = FaceWorkerSettings()
    configure_logging(
        service_name=SERVICE_NAME,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    logger = get_logger(__name__)
    # The database URL and the storage keys are secrets and are not logged.
    logger.info(
        "face_worker_starting",
        interpol_base_url=settings.interpol_base_url,
        storage_endpoint_url=settings.storage_endpoint_url,
        storage_bucket=settings.storage_bucket,
    )

    stop = threading.Event()

    def request_stop(signal_number: int, _frame: FrameType | None) -> None:
        logger.info("face_worker_stopping", signal=signal.Signals(signal_number).name)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    # Loaded before anything else runs, so a missing model file stops the start at once.
    faces = FaceAnalyzer.load(settings.detector_model_path, settings.recognizer_model_path)
    # pool_pre_ping replaces connections the database closed while the service waited.
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    service = FaceWorkerService(
        queue=PhotoRepository(engine.begin),
        # The client keeps time.sleep: polite delays are never skipped, even on shutdown.
        photos=PhotoClient(CurlTransport(), settings.interpol_base_url),
        store=S3PhotoStore.connect(
            settings.storage_endpoint_url,
            settings.storage_access_key.get_secret_value(),
            settings.storage_secret_key.get_secret_value(),
            settings.storage_bucket,
        ),
        faces=faces,
        # An idle wait ends at once when a stop is requested.
        sleep=stop.wait,
        idle_seconds=settings.idle_seconds,
    )
    try:
        service.run(should_stop=stop.is_set)
    except Exception:
        # A programming error: log it and exit non-zero so it is noticed.
        logger.exception("face_worker_crashed")
        raise
    finally:
        engine.dispose()
    logger.info("face_worker_stopped")
