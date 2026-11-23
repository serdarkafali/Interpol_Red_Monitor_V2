"""The face worker's main loop (ADR 0011).

One step either downloads the next pending photo or, once a day, deletes files in the
store that the database no longer refers to. Shutdown is honoured between steps.
"""

import hashlib
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import OperationalError

from interpol_face_worker.interpol_photos import PhotoDownloadError
from interpol_face_worker.photo_store import StoredObject, object_key
from interpol_face_worker.repository import MAX_DOWNLOAD_ATTEMPTS, PendingPhoto
from interpol_observability import get_logger

# The store or the database being unreachable is not the photo's fault: the step is
# abandoned without counting an attempt and tried again after the idle wait.
UNAVAILABLE_ERRORS = (BotoCoreError, ClientError, OperationalError)

logger = get_logger(__name__)


class PhotoSource(Protocol):
    """Where photos are downloaded from (the Interpol photo client)."""

    def download(self, entity_id: str, picture_id: str) -> bytes: ...


class PhotoStore(Protocol):
    """Where photo files are kept (the S3 photo store)."""

    def put(self, key: str, content: bytes) -> None: ...

    def list_objects(self) -> Iterable[StoredObject]: ...

    def delete(self, key: str) -> None: ...


class PhotoQueue(Protocol):
    """The database side: which photos to download and what happened to them."""

    def next_pending_photo(self, now: datetime) -> PendingPhoto | None: ...

    def record_download(
        self, photo_id: int, object_key: str, sha256: str, now: datetime
    ) -> bool: ...

    def record_failure(self, photo_id: int, now: datetime) -> None: ...

    def stored_object_keys(self) -> set[str]: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


class FaceWorkerService:
    """Downloads pending photos one at a time and cleans up orphaned files daily."""

    def __init__(
        self,
        queue: PhotoQueue,
        photos: PhotoSource,
        store: PhotoStore,
        *,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], object] = time.sleep,
        idle_seconds: float = 60.0,
        cleanup_interval: timedelta = timedelta(days=1),
        orphan_grace: timedelta = timedelta(hours=1),
    ) -> None:
        self._queue = queue
        self._photos = photos
        self._store = store
        self._now = now
        self._sleep = sleep
        self._idle_seconds = idle_seconds
        self._cleanup_interval = cleanup_interval
        self._orphan_grace = orphan_grace
        # None: no cleanup yet, so the first step after a start runs one.
        self._last_cleanup: datetime | None = None

    def run(self, should_stop: Callable[[], bool]) -> None:
        """Run steps until ``should_stop`` returns True."""
        while not should_stop():
            self.step()

    def step(self) -> None:
        """Clean up if it is due, otherwise download one photo or wait."""
        try:
            if self._cleanup_due():
                self._clean_up()
                return
            photo = self._queue.next_pending_photo(self._now())
            if photo is None:
                self._sleep(self._idle_seconds)
                return
            self._process(photo)
        except UNAVAILABLE_ERRORS as exc:
            # Only the error class: this repeats every idle wait while the outage lasts.
            logger.warning("dependency_unavailable", error=type(exc).__name__)
            self._sleep(self._idle_seconds)

    def _cleanup_due(self) -> bool:
        if self._last_cleanup is None:
            return True
        return self._now() - self._last_cleanup >= self._cleanup_interval

    def _process(self, photo: PendingPhoto) -> None:
        """Download one photo, store it and record the result."""
        try:
            content = self._photos.download(photo.entity_id, photo.picture_id)
        except PhotoDownloadError as exc:
            self._queue.record_failure(photo.photo_id, self._now())
            attempts = photo.download_attempts + 1
            event = (
                "photo_download_given_up"
                if attempts >= MAX_DOWNLOAD_ATTEMPTS
                else "photo_download_failed"
            )
            logger.warning(
                event,
                entity_id=photo.entity_id,
                picture_id=photo.picture_id,
                reason=exc.reason,
                attempts=attempts,
            )
            return

        key = object_key(photo.entity_id, photo.picture_id)
        # File first, database second: a crash in between leaves an orphaned file for the
        # cleanup, never a row that claims a file which does not exist.
        self._store.put(key, content)
        sha256 = hashlib.sha256(content).hexdigest()
        if self._queue.record_download(photo.photo_id, key, sha256, self._now()):
            logger.info("photo_stored", entity_id=photo.entity_id, picture_id=photo.picture_id)
        else:
            # The ingestor dropped the photo meanwhile; the cleanup will delete the file.
            logger.info("photo_row_gone", entity_id=photo.entity_id, picture_id=photo.picture_id)

    def _clean_up(self) -> None:
        """Delete files the database does not refer to and that are older than the grace."""
        now = self._now()
        known = self._queue.stored_object_keys()
        cutoff = now - self._orphan_grace
        deleted = 0
        for stored in self._store.list_objects():
            if stored.key not in known and stored.last_modified < cutoff:
                self._store.delete(stored.key)
                deleted += 1
        self._last_cleanup = now
        logger.info("orphan_photos_deleted", count=deleted)
