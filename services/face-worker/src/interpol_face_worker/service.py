"""The face worker's main loop (ADR 0011, ADR 0012).

One step does one thing, in this order: once a day, delete files in the store that the
database no longer refers to; otherwise find the faces on the next downloaded photo;
otherwise download the next pending photo; otherwise wait. Shutdown is honoured
between steps.
"""

import hashlib
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import OperationalError

from interpol_face_worker.face_analysis import DetectedFace, FaceAnalysisError
from interpol_face_worker.interpol_photos import PhotoDownloadError
from interpol_face_worker.photo_store import StoredObject, object_key
from interpol_face_worker.repository import (
    MAX_DOWNLOAD_ATTEMPTS,
    FaceMatches,
    PendingPhoto,
    PhotoForFaces,
)
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

    def get(self, key: str) -> bytes: ...

    def list_objects(self) -> Iterable[StoredObject]: ...

    def delete(self, key: str) -> None: ...


class PhotoQueue(Protocol):
    """The database side: which photos to process and what happened to them."""

    def next_pending_photo(self, now: datetime) -> PendingPhoto | None: ...

    def record_download(
        self, photo_id: int, object_key: str, sha256: str, now: datetime
    ) -> bool: ...

    def record_failure(self, photo_id: int, now: datetime) -> None: ...

    def stored_object_keys(self) -> set[str]: ...

    def next_photo_for_faces(self) -> PhotoForFaces | None: ...

    def record_faces(
        self, photo_id: int, faces: Sequence[DetectedFace], now: datetime
    ) -> list[FaceMatches] | None: ...


class FaceDetector(Protocol):
    """What finds the faces on a photo (the YuNet + SFace face analyzer)."""

    def analyze(self, jpeg: bytes) -> list[DetectedFace]: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


class FaceWorkerService:
    """Downloads photos, finds their faces one photo at a time, cleans up daily."""

    def __init__(
        self,
        queue: PhotoQueue,
        photos: PhotoSource,
        store: PhotoStore,
        faces: FaceDetector,
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
        self._faces = faces
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
        """Clean up if it is due, otherwise analyse or download one photo, or wait."""
        try:
            if self._cleanup_due():
                self._clean_up()
                return
            # Faces before downloads: a downloaded photo is analysed at once, even while
            # a long download backlog is still being worked through.
            photo_for_faces = self._queue.next_photo_for_faces()
            if photo_for_faces is not None:
                self._process_faces(photo_for_faces)
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

    def _process_faces(self, photo: PhotoForFaces) -> None:
        """Find the faces on one downloaded photo and save them with their matches.

        A missing file or a photo that cannot be decoded counts as a photo without
        faces: it is marked as analysed and logged, so it is not retried forever.
        """
        try:
            content = self._store.get(photo.object_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "NoSuchKey":
                raise  # the store itself failed: step() waits and tries again
            logger.warning("photo_file_missing", photo_id=photo.photo_id)
            faces: list[DetectedFace] = []
        else:
            try:
                faces = self._faces.analyze(content)
            except FaceAnalysisError as exc:
                logger.warning("face_analysis_failed", photo_id=photo.photo_id, reason=exc.reason)
                faces = []

        results = self._queue.record_faces(photo.photo_id, faces, self._now())
        if results is None:
            # The ingestor dropped the photo meanwhile; nothing was written.
            logger.info("photo_row_gone", photo_id=photo.photo_id)
            return

        # Only ids and distances: a possible match is for human review, not a verdict.
        logger.info("faces_analysed", photo_id=photo.photo_id, faces=len(faces))
        for result in results:
            for match in result.other_notices:
                logger.warning(
                    "possible_match",
                    face_id=result.face_id,
                    match_face_id=match.face_id,
                    match_notice_id=match.notice_id,
                    distance=round(match.distance, 3),
                )
            for mismatch in result.mismatches:
                logger.warning(
                    "notice_photo_mismatch",
                    face_id=result.face_id,
                    other_face_id=mismatch.face_id,
                    distance=round(mismatch.distance, 3),
                )

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
