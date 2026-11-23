"""Tests of the face worker's main loop with fakes; no network, store or database.

The file is not called test_service.py: these test folders are not packages, so mypy
names every test module by its file name, and the collector already has a test_service.
"""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import EndpointConnectionError
from structlog.testing import capture_logs

from interpol_face_worker.interpol_photos import JPEG_SIGNATURE, PhotoDownloadError
from interpol_face_worker.photo_store import StoredObject
from interpol_face_worker.repository import MAX_DOWNLOAD_ATTEMPTS, PendingPhoto
from interpol_face_worker.service import FaceWorkerService

START = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
PHOTO = JPEG_SIGNATURE + b"made-up photo"


class Clock:
    def __init__(self) -> None:
        self.current = START

    def __call__(self) -> datetime:
        return self.current


class FakeQueue:
    """An in-memory notice_photos table."""

    def __init__(self, *pending: PendingPhoto) -> None:
        self.pending = list(pending)
        self.downloads: list[tuple[int, str, str]] = []
        self.failures: list[int] = []
        self.keys: set[str] = set()
        self.row_gone = False

    def next_pending_photo(self, now: datetime) -> PendingPhoto | None:
        return self.pending.pop(0) if self.pending else None

    def record_download(self, photo_id: int, object_key: str, sha256: str, now: datetime) -> bool:
        if self.row_gone:
            return False
        self.downloads.append((photo_id, object_key, sha256))
        self.keys.add(object_key)
        return True

    def record_failure(self, photo_id: int, now: datetime) -> None:
        self.failures.append(photo_id)

    def stored_object_keys(self) -> set[str]:
        return set(self.keys)


class FakePhotos:
    def __init__(self, result: bytes | PhotoDownloadError = PHOTO) -> None:
        self.result = result
        self.requests: list[tuple[str, str]] = []

    def download(self, entity_id: str, picture_id: str) -> bytes:
        self.requests.append((entity_id, picture_id))
        if isinstance(self.result, PhotoDownloadError):
            raise self.result
        return self.result


class FakeStore:
    """An in-memory bucket: key → (content, upload time)."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.files: dict[str, tuple[bytes, datetime]] = {}
        self.fail_with: Exception | None = None

    def put(self, key: str, content: bytes) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.files[key] = (content, self.clock())

    def list_objects(self) -> Iterator[StoredObject]:
        for key, (_, uploaded) in list(self.files.items()):
            yield StoredObject(key=key, last_modified=uploaded)

    def delete(self, key: str) -> None:
        self.files.pop(key, None)


def no_sleep(_seconds: float) -> None:
    pass


def pending(attempts: int = 0) -> PendingPhoto:
    return PendingPhoto(
        photo_id=7, entity_id="1900/1", picture_id="111", download_attempts=attempts
    )


def make_service(
    queue: FakeQueue, photos: FakePhotos, store: FakeStore, clock: Clock, sleeps: list[float]
) -> FaceWorkerService:
    service = FaceWorkerService(queue, photos, store, now=clock, sleep=sleeps.append)
    service.step()  # the first step after a start is the cleanup; get it out of the way
    sleeps.clear()
    return service


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_a_pending_photo_is_stored_then_recorded(clock: Clock) -> None:
    queue, photos, store = FakeQueue(pending()), FakePhotos(), FakeStore(clock)
    sleeps: list[float] = []
    service = make_service(queue, photos, store, clock, sleeps)

    with capture_logs() as logs:
        service.step()

    assert photos.requests == [("1900/1", "111")]
    assert store.files["notices/1900-1/111"][0] == PHOTO
    assert queue.downloads == [(7, "notices/1900-1/111", hashlib.sha256(PHOTO).hexdigest())]
    assert sleeps == []
    assert logs[-1]["event"] == "photo_stored"
    assert logs[-1]["entity_id"] == "1900/1"


def test_nothing_pending_waits_the_idle_time(clock: Clock) -> None:
    queue, store = FakeQueue(), FakeStore(clock)
    sleeps: list[float] = []
    service = make_service(queue, FakePhotos(), store, clock, sleeps)

    service.step()

    assert sleeps == [60.0]


def test_a_failed_download_is_recorded_and_logged_without_the_photo(clock: Clock) -> None:
    queue, store = FakeQueue(pending()), FakeStore(clock)
    sleeps: list[float] = []
    photos = FakePhotos(PhotoDownloadError("not_found"))
    service = make_service(queue, photos, store, clock, sleeps)

    with capture_logs() as logs:
        service.step()

    assert queue.failures == [7]
    assert store.files == {}
    assert logs[-1] == {
        "event": "photo_download_failed",
        "log_level": "warning",
        "entity_id": "1900/1",
        "picture_id": "111",
        "reason": "not_found",
        "attempts": 1,
    }


def test_the_last_allowed_failure_is_logged_as_given_up(clock: Clock) -> None:
    queue = FakeQueue(pending(attempts=MAX_DOWNLOAD_ATTEMPTS - 1))
    photos = FakePhotos(PhotoDownloadError("unreachable"))
    service = make_service(queue, photos, FakeStore(clock), clock, sleeps=[])

    with capture_logs() as logs:
        service.step()

    assert logs[-1]["event"] == "photo_download_given_up"
    assert logs[-1]["attempts"] == MAX_DOWNLOAD_ATTEMPTS


def test_a_row_deleted_during_the_download_leaves_the_file_for_the_cleanup(
    clock: Clock,
) -> None:
    queue, store = FakeQueue(pending()), FakeStore(clock)
    queue.row_gone = True
    service = make_service(queue, FakePhotos(), store, clock, sleeps=[])

    with capture_logs() as logs:
        service.step()

    assert "notices/1900-1/111" in store.files
    assert logs[-1]["event"] == "photo_row_gone"


def test_an_unreachable_store_counts_no_attempt_and_waits(clock: Clock) -> None:
    queue, store = FakeQueue(pending()), FakeStore(clock)
    sleeps: list[float] = []
    store.fail_with = EndpointConnectionError(endpoint_url="http://seaweedfs:8333")
    service = make_service(queue, FakePhotos(), store, clock, sleeps)

    with capture_logs() as logs:
        service.step()

    assert queue.failures == []
    assert queue.downloads == []
    assert sleeps == [60.0]
    assert logs[-1]["event"] == "dependency_unavailable"


def test_cleanup_deletes_only_old_files_the_database_does_not_know(clock: Clock) -> None:
    queue, store = FakeQueue(), FakeStore(clock)
    queue.keys = {"notices/1900-1/known"}
    store.files = {
        "notices/1900-1/known": (PHOTO, START - timedelta(days=3)),
        "notices/1900-1/orphan": (PHOTO, START - timedelta(hours=2)),
        # Uploaded moments ago, its row may not be updated yet: kept.
        "notices/1900-1/fresh": (PHOTO, START - timedelta(minutes=10)),
    }
    service = FaceWorkerService(queue, FakePhotos(), store, now=clock, sleep=no_sleep)

    with capture_logs() as logs:
        service.step()

    assert set(store.files) == {"notices/1900-1/known", "notices/1900-1/fresh"}
    assert logs[-1] == {"event": "orphan_photos_deleted", "log_level": "info", "count": 1}


def test_cleanup_runs_at_start_and_then_once_a_day(clock: Clock) -> None:
    queue, store = FakeQueue(), FakeStore(clock)
    service = FaceWorkerService(queue, FakePhotos(), store, now=clock, sleep=no_sleep)

    def cleanups_in_one_step() -> int:
        with capture_logs() as logs:
            service.step()
        return sum(1 for log in logs if log["event"] == "orphan_photos_deleted")

    assert cleanups_in_one_step() == 1
    clock.current = START + timedelta(hours=23)
    assert cleanups_in_one_step() == 0
    clock.current = START + timedelta(days=1)
    assert cleanups_in_one_step() == 1


def test_run_stops_when_asked(clock: Clock) -> None:
    queue = FakeQueue()
    sleeps: list[float] = []
    service = FaceWorkerService(
        queue, FakePhotos(), FakeStore(clock), now=clock, sleep=sleeps.append
    )
    answers = iter([False, False, True])

    service.run(lambda: next(answers))

    # Step 1 is the cleanup, step 2 finds nothing and waits; then the loop stops.
    assert sleeps == [60.0]
