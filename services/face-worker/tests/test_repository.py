"""Tests of the face worker's queries against a real, migrated PostgreSQL container.

The fixtures live here and not in a conftest.py: mypy checks every test folder, and a
third top-level ``conftest`` module would clash with the other packages' ones. All
notices and photos are made up.
"""

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, delete, func, insert, select, text
from testcontainers.community.postgres import PostgresContainer

from interpol_db import EMBEDDING_DIMENSIONS, Face, FaceMatch, Notice, NoticePhoto
from interpol_face_worker.face_analysis import MODEL_NAME, DetectedFace
from interpol_face_worker.repository import (
    MAX_DOWNLOAD_ATTEMPTS,
    MAX_MATCH_DISTANCE,
    FaceMatches,
    PendingPhoto,
    PhotoForFaces,
    PhotoRepository,
    SimilarFace,
    find_similar_faces,
    next_pending_photo,
    next_photo_for_faces,
    record_download,
    record_faces,
    record_failure,
    record_matches,
    same_notice_faces,
    stored_object_keys,
)

# Same image as docker-compose.yml.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.2-pg18"
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "libs" / "db" / "alembic.ini"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine of a throwaway database migrated to the latest revision."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        with pytest.MonkeyPatch.context() as monkeypatch:
            # env.py prefers DATABASE_URL, so the development database is never touched.
            monkeypatch.setenv("DATABASE_URL", url)
            command.upgrade(Config(ALEMBIC_INI), "head")
        engine = create_engine(url)
        yield engine
        engine.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back after the test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


def add_notice(connection: Connection, entity_id: str, *, removed: bool = False) -> int:
    """Insert a made-up notice and return its id."""
    return connection.execute(
        insert(Notice)
        .values(
            entity_id=entity_id,
            name="TEST",
            content_hash="hash",
            deleted_at=NOW - timedelta(days=1) if removed else None,
        )
        .returning(Notice.id)
    ).scalar_one()


def add_photo(
    connection: Connection,
    notice_id: int,
    picture_id: str,
    *,
    attempts: int = 0,
    last_attempt_at: datetime | None = None,
    downloaded: bool = False,
    faces_processed: bool = False,
) -> int:
    """Insert a photo row and return its id."""
    return connection.execute(
        insert(NoticePhoto)
        .values(
            notice_id=notice_id,
            picture_id=picture_id,
            download_attempts=attempts,
            last_attempt_at=last_attempt_at,
            object_key=f"notices/x/{picture_id}" if downloaded else None,
            sha256="abc" if downloaded else None,
            downloaded_at=NOW if downloaded else None,
            faces_processed_at=NOW if faces_processed else None,
        )
        .returning(NoticePhoto.id)
    ).scalar_one()


def embedding(degrees: float) -> list[float]:
    """A made-up unit vector; two of them are 1 - cos(angle between them) apart.

    0 degrees apart is distance 0, 30 is about 0.13, 60 is 0.5 and 90 is 1.
    """
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)] + [0.0] * (EMBEDDING_DIMENSIONS - 2)


def detected(degrees: float) -> DetectedFace:
    return DetectedFace(10, 20, 30, 40, 0.95, embedding(degrees))


def add_face(
    connection: Connection, photo_id: int, degrees: float, *, model: str = MODEL_NAME
) -> int:
    """Insert a made-up face of a photo and return its id."""
    return connection.execute(
        insert(Face)
        .values(
            photo_id=photo_id,
            box_x=10,
            box_y=20,
            box_width=30,
            box_height=40,
            detection_score=0.95,
            embedding=embedding(degrees),
            model=model,
        )
        .returning(Face.id)
    ).scalar_one()


def add_notice_with_face(
    connection: Connection, entity_id: str, degrees: float, *, removed: bool = False
) -> tuple[int, int]:
    """A notice with one analysed photo and one face; returns the notice and face ids."""
    notice_id = add_notice(connection, entity_id, removed=removed)
    photo_id = add_photo(
        connection, notice_id, f"{entity_id}-photo", downloaded=True, faces_processed=True
    )
    return notice_id, add_face(connection, photo_id, degrees)


def match_rows(connection: Connection) -> list[tuple[int, int, bool]]:
    rows = connection.execute(
        select(FaceMatch.first_face_id, FaceMatch.second_face_id, FaceMatch.same_notice).order_by(
            FaceMatch.first_face_id, FaceMatch.second_face_id
        )
    )
    return [tuple(row) for row in rows]  # type: ignore[misc]


def photo_row(connection: Connection, photo_id: int) -> tuple[object, ...]:
    row = connection.execute(
        select(
            NoticePhoto.object_key,
            NoticePhoto.sha256,
            NoticePhoto.downloaded_at,
            NoticePhoto.download_attempts,
            NoticePhoto.last_attempt_at,
        ).where(NoticePhoto.id == photo_id)
    ).one()
    return tuple(row)


def test_a_new_photo_is_pending_with_its_entity_id(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    photo_id = add_photo(connection, notice_id, "111")

    assert next_pending_photo(connection, NOW) == PendingPhoto(
        photo_id=photo_id, entity_id="1900/1", picture_id="111", download_attempts=0
    )


def test_nothing_is_pending_in_an_empty_table(connection: Connection) -> None:
    assert next_pending_photo(connection, NOW) is None


def test_downloaded_photos_and_removed_notices_are_skipped(connection: Connection) -> None:
    add_photo(connection, add_notice(connection, "1900/1"), "111", downloaded=True)
    add_photo(connection, add_notice(connection, "1900/2", removed=True), "222")

    assert next_pending_photo(connection, NOW) is None


def test_never_tried_photos_come_before_retries(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    add_photo(connection, notice_id, "111", attempts=1, last_attempt_at=NOW - timedelta(days=1))
    fresh = add_photo(connection, notice_id, "222")

    pending = next_pending_photo(connection, NOW)

    assert pending is not None
    assert pending.photo_id == fresh


def test_the_oldest_attempt_is_retried_first(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    add_photo(connection, notice_id, "111", attempts=1, last_attempt_at=NOW - timedelta(hours=3))
    oldest = add_photo(
        connection, notice_id, "222", attempts=1, last_attempt_at=NOW - timedelta(hours=5)
    )

    pending = next_pending_photo(connection, NOW)

    assert pending is not None
    assert pending.photo_id == oldest


@pytest.mark.parametrize(
    ("attempts", "delay"),
    [(1, timedelta(hours=1)), (2, timedelta(hours=2)), (3, timedelta(hours=4))],
)
def test_a_failed_photo_waits_a_doubling_delay(
    connection: Connection, attempts: int, delay: timedelta
) -> None:
    notice_id = add_notice(connection, "1900/1")
    last_attempt = NOW - delay
    add_photo(connection, notice_id, "111", attempts=attempts, last_attempt_at=last_attempt)

    # One second before the delay has passed: not yet; exactly when it passed: due.
    assert next_pending_photo(connection, NOW - timedelta(seconds=1)) is None
    assert next_pending_photo(connection, NOW) is not None


def test_a_photo_is_given_up_after_the_last_attempt(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    long_ago = NOW - timedelta(days=30)
    add_photo(
        connection, notice_id, "111", attempts=MAX_DOWNLOAD_ATTEMPTS, last_attempt_at=long_ago
    )

    assert next_pending_photo(connection, NOW) is None


def test_record_download_fills_the_file_columns(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")

    assert record_download(connection, photo_id, "notices/1900-1/111", "f00d", NOW) is True

    assert photo_row(connection, photo_id)[:3] == ("notices/1900-1/111", "f00d", NOW)
    assert next_pending_photo(connection, NOW) is None


def test_record_download_of_a_deleted_row_reports_false(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")
    # The ingestor drops the photo while it is being downloaded.
    connection.execute(delete(NoticePhoto).where(NoticePhoto.id == photo_id))

    assert record_download(connection, photo_id, "notices/1900-1/111", "f00d", NOW) is False


def test_record_download_does_not_overwrite_an_earlier_download(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111", downloaded=True)

    assert record_download(connection, photo_id, "notices/other", "beef", NOW) is False
    assert photo_row(connection, photo_id)[0] == "notices/x/111"


def test_record_failure_counts_the_attempt(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")

    record_failure(connection, photo_id, NOW)
    record_failure(connection, photo_id, NOW + timedelta(hours=1))

    assert photo_row(connection, photo_id)[3:] == (2, NOW + timedelta(hours=1))


def test_stored_object_keys_lists_only_downloaded_photos(connection: Connection) -> None:
    notice_id = add_notice(connection, "1900/1")
    add_photo(connection, notice_id, "111", downloaded=True)
    add_photo(connection, notice_id, "222")

    assert stored_object_keys(connection) == {"notices/x/111"}


def test_photo_repository_commits_each_call(engine: Engine) -> None:
    """The service's view: every call runs and commits in its own transaction."""
    repository = PhotoRepository(engine.begin)
    try:
        with engine.begin() as connection:
            photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111")

        assert repository.next_pending_photo(NOW) is not None
        repository.record_failure(photo_id, NOW - timedelta(hours=2))
        assert repository.record_download(photo_id, "notices/1900-1/111", "f00d", NOW) is True
        assert repository.next_pending_photo(NOW) is None
        assert repository.stored_object_keys() == {"notices/1900-1/111"}
    finally:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE notices CASCADE"))


# --- Faces (Faz 5) -----------------------------------------------------------------------


def test_next_photo_for_faces_picks_a_downloaded_photo_not_analysed_yet(
    connection: Connection,
) -> None:
    notice_id = add_notice(connection, "1900/1")
    add_photo(connection, notice_id, "111")  # not downloaded
    add_photo(connection, notice_id, "222", downloaded=True, faces_processed=True)
    photo_id = add_photo(connection, notice_id, "333", downloaded=True)
    removed_id = add_notice(connection, "1900/2", removed=True)
    add_photo(connection, removed_id, "444", downloaded=True)

    assert next_photo_for_faces(connection) == PhotoForFaces(
        photo_id=photo_id, object_key="notices/x/333"
    )


def test_next_photo_for_faces_is_none_when_everything_is_analysed(
    connection: Connection,
) -> None:
    add_photo(
        connection, add_notice(connection, "1900/1"), "111", downloaded=True, faces_processed=True
    )

    assert next_photo_for_faces(connection) is None


def test_record_faces_saves_every_face_and_marks_the_photo(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111", downloaded=True)

    face_ids = record_faces(connection, photo_id, [detected(0), detected(90)], NOW)

    assert face_ids is not None and len(face_ids) == 2
    rows = connection.execute(
        select(Face.id, Face.photo_id, Face.box_width, Face.model, Face.embedding).order_by(Face.id)
    ).all()
    # The ids come back in the order of the faces given.
    assert [row.id for row in rows] == face_ids
    assert {(row.photo_id, row.box_width, row.model) for row in rows} == {
        (photo_id, 30, MODEL_NAME)
    }
    assert list(rows[1].embedding[:2]) == pytest.approx([0.0, 1.0], abs=1e-6)
    processed_at = connection.execute(
        select(NoticePhoto.faces_processed_at).where(NoticePhoto.id == photo_id)
    ).scalar_one()
    assert processed_at == NOW


def test_record_faces_without_faces_only_marks_the_photo(connection: Connection) -> None:
    photo_id = add_photo(connection, add_notice(connection, "1900/1"), "111", downloaded=True)

    assert record_faces(connection, photo_id, [], NOW) == []
    assert next_photo_for_faces(connection) is None
    assert connection.execute(select(func.count()).select_from(Face)).scalar_one() == 0


def test_record_faces_of_a_deleted_or_analysed_photo_writes_nothing(
    connection: Connection,
) -> None:
    notice_id = add_notice(connection, "1900/1")
    analysed_id = add_photo(connection, notice_id, "111", downloaded=True, faces_processed=True)
    deleted_id = add_photo(connection, notice_id, "222", downloaded=True)
    connection.execute(delete(NoticePhoto).where(NoticePhoto.id == deleted_id))

    assert record_faces(connection, analysed_id, [detected(0)], NOW) is None
    assert record_faces(connection, deleted_id, [detected(0)], NOW) is None
    assert connection.execute(select(func.count()).select_from(Face)).scalar_one() == 0


def test_find_similar_faces_returns_close_faces_of_other_notices_nearest_first(
    connection: Connection,
) -> None:
    _, face_id = add_notice_with_face(connection, "1900/1", 0)
    near_notice, near_face = add_notice_with_face(connection, "1900/2", 10)
    nearer_notice, nearer_face = add_notice_with_face(connection, "1900/3", 5)
    add_notice_with_face(connection, "1900/4", 80)  # too far: about 0.83
    add_notice_with_face(connection, "1900/5", 1, removed=True)

    found = find_similar_faces(connection, face_id)

    assert [(face.face_id, face.notice_id) for face in found] == [
        (nearer_face, nearer_notice),
        (near_face, near_notice),
    ]
    assert found[0].distance == pytest.approx(1 - math.cos(math.radians(5)), abs=1e-6)


def test_find_similar_faces_leaves_out_the_same_notice_and_other_models(
    connection: Connection,
) -> None:
    notice_id, face_id = add_notice_with_face(connection, "1900/1", 0)
    second_photo = add_photo(connection, notice_id, "222", downloaded=True, faces_processed=True)
    add_face(connection, second_photo, 0)
    other_notice = add_notice(connection, "1900/2")
    other_photo = add_photo(connection, other_notice, "333", downloaded=True)
    add_face(connection, other_photo, 0, model="another-model")

    assert find_similar_faces(connection, face_id) == []


def test_find_similar_faces_respects_the_limit_and_unknown_faces(
    connection: Connection,
) -> None:
    _, face_id = add_notice_with_face(connection, "1900/1", 0)
    for number in range(2, 6):
        add_notice_with_face(connection, f"1900/{number}", number)

    assert len(find_similar_faces(connection, face_id, limit=2)) == 2
    assert find_similar_faces(connection, -1) == []


def test_same_notice_faces_returns_every_other_face_of_the_notice(
    connection: Connection,
) -> None:
    notice_id, face_id = add_notice_with_face(connection, "1900/1", 0)
    second_photo = add_photo(connection, notice_id, "222", downloaded=True, faces_processed=True)
    close_face = add_face(connection, second_photo, 20)
    far_face = add_face(connection, second_photo, 90)  # kept even above the threshold
    add_notice_with_face(connection, "1900/2", 0)

    found = same_notice_faces(connection, face_id)

    assert [face.face_id for face in found] == [close_face, far_face]
    assert {face.notice_id for face in found} == {notice_id}
    assert found[1].distance == pytest.approx(1.0, abs=1e-6)
    assert same_notice_faces(connection, -1) == []


def test_record_matches_stores_each_pair_once_with_the_smaller_id_first(
    connection: Connection,
) -> None:
    notice_id, first = add_notice_with_face(connection, "1900/1", 0)
    photo_id = add_photo(connection, notice_id, "222", downloaded=True, faces_processed=True)
    second = add_face(connection, photo_id, 30)
    pair = SimilarFace(face_id=first, photo_id=0, notice_id=notice_id, distance=0.13)
    reverse = SimilarFace(face_id=second, photo_id=0, notice_id=notice_id, distance=0.13)

    record_matches(connection, second, [pair], same_notice=True)
    record_matches(connection, first, [reverse], same_notice=True)
    record_matches(connection, first, [], same_notice=False)

    assert match_rows(connection) == [(first, second, True)]


def test_mismatches_are_same_notice_faces_at_or_above_the_threshold() -> None:
    close = SimilarFace(face_id=2, photo_id=1, notice_id=1, distance=0.2)
    far = SimilarFace(face_id=3, photo_id=1, notice_id=1, distance=MAX_MATCH_DISTANCE)

    assert FaceMatches(face_id=1, same_notice=[close, far], other_notices=[]).mismatches == [far]


def test_photo_repository_saves_faces_and_their_matches_together(engine: Engine) -> None:
    """The service's view: one call analyses a photo, compares and commits everything."""
    repository = PhotoRepository(engine.begin)
    try:
        with engine.begin() as connection:
            notice_id, old_face = add_notice_with_face(connection, "1900/1", 0)
            other_notice, other_face = add_notice_with_face(connection, "1900/2", 10)
            add_notice_with_face(connection, "1900/3", 180)  # not similar to anything
            photo_id = add_photo(connection, notice_id, "222", downloaded=True)

        assert repository.next_photo_for_faces() == PhotoForFaces(photo_id, "notices/x/222")
        # One face like the notice's own photo, one that looks like somebody else.
        results = repository.record_faces(photo_id, [detected(2), detected(90)], NOW)

        assert results is not None
        same_face, odd_face = (result.face_id for result in results)
        assert [face.face_id for face in results[0].same_notice] == [old_face, odd_face]
        assert results[0].mismatches == [results[0].same_notice[1]]
        assert [(face.face_id, face.notice_id) for face in results[0].other_notices] == [
            (other_face, other_notice)
        ]
        assert results[1].other_notices == []
        assert repository.next_photo_for_faces() is None
        assert repository.record_faces(photo_id, [detected(0)], NOW) is None
        with engine.connect() as connection:
            rows = match_rows(connection)
        assert (old_face, same_face, True) in rows
        assert (old_face, odd_face, True) in rows
        assert (same_face, odd_face, True) in rows
        assert (other_face, same_face, False) in rows
        assert len(rows) == len(set(rows))
    finally:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE notices CASCADE"))
