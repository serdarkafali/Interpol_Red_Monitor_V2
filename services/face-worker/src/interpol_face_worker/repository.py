"""Database reads and writes of the face worker (ADR 0011).

The photos to download are chosen from ``notice_photos``, not from Kafka. Every function
works on a ``Connection`` inside a transaction owned by the caller; nothing here commits.
The face worker writes only the download and face columns of ``notice_photos`` and the
``faces`` and ``face_matches`` tables; notices and photo rows are added and deleted by
the ingestor.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, Integer, Row, cast, func, insert, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from interpol_db import Face, FaceMatch, Notice, NoticePhoto
from interpol_face_worker.face_analysis import MODEL_NAME, DetectedFace

# A function that opens a transaction and yields its connection, e.g. ``engine.begin``.
Transaction = Callable[[], AbstractContextManager[Connection]]

# After this many failed downloads a photo is not tried again (ADR 0011).
MAX_DOWNLOAD_ATTEMPTS = 5
# The wait before the next attempt doubles with every failure: 1, 2, 4, 8 hours.
FIRST_RETRY_DELAY_HOURS = 1

# Cosine distance of the 0.443 similarity threshold of ADR 0012 (1 - 0.443). Closer faces
# are only a possible match for human review, never an identification.
MAX_MATCH_DISTANCE = 0.557
# At most this many possible matches of other notices are kept for one face.
SIMILAR_FACES_LIMIT = 10


@dataclass(frozen=True)
class PendingPhoto:
    """A photo that should be downloaded now."""

    photo_id: int
    entity_id: str
    picture_id: str
    download_attempts: int


@dataclass(frozen=True)
class PhotoForFaces:
    """A downloaded photo whose faces are not analysed yet."""

    photo_id: int
    object_key: str


@dataclass(frozen=True)
class SimilarFace:
    """Another face compared with the searched face."""

    face_id: int
    photo_id: int
    notice_id: int
    # Cosine distance: 0 is the same direction, MAX_MATCH_DISTANCE is the threshold.
    distance: float


@dataclass(frozen=True)
class FaceMatches:
    """The automatic comparisons of one new face, as saved in ``face_matches``."""

    face_id: int
    # Every other face of the same notice, nearest first, whatever the distance.
    same_notice: list[SimilarFace]
    # Faces of other active notices below MAX_MATCH_DISTANCE, nearest first.
    other_notices: list[SimilarFace]

    @property
    def mismatches(self) -> list[SimilarFace]:
        """Faces of the same notice that do not look like the same person."""
        return [face for face in self.same_notice if face.distance >= MAX_MATCH_DISTANCE]


def next_pending_photo(connection: Connection, now: datetime) -> PendingPhoto | None:
    """Return the next photo to download, or None if nothing is due.

    Photos never tried come first, then the oldest attempt. A photo is skipped while its
    notice is removed, after the last allowed attempt, and until its retry delay passed.
    """
    # make_interval(years, months, weeks, days, hours): 1, 2, 4, 8 hours after 1-4 failures.
    retry_hours = FIRST_RETRY_DELAY_HOURS * cast(
        func.power(2, NoticePhoto.download_attempts - 1), Integer
    )
    retry_delay = func.make_interval(0, 0, 0, 0, retry_hours)
    statement = (
        select(
            NoticePhoto.id,
            Notice.entity_id,
            NoticePhoto.picture_id,
            NoticePhoto.download_attempts,
        )
        .join(Notice, Notice.id == NoticePhoto.notice_id)
        .where(
            NoticePhoto.downloaded_at.is_(None),
            Notice.deleted_at.is_(None),
            NoticePhoto.download_attempts < MAX_DOWNLOAD_ATTEMPTS,
            or_(
                NoticePhoto.last_attempt_at.is_(None),
                NoticePhoto.last_attempt_at <= literal(now) - retry_delay,
            ),
        )
        .order_by(NoticePhoto.last_attempt_at.asc().nulls_first(), NoticePhoto.id)
        .limit(1)
    )
    row = connection.execute(statement).first()
    if row is None:
        return None
    return PendingPhoto(
        photo_id=row.id,
        entity_id=row.entity_id,
        picture_id=row.picture_id,
        download_attempts=row.download_attempts,
    )


def record_download(
    connection: Connection, photo_id: int, object_key: str, sha256: str, now: datetime
) -> bool:
    """Mark a photo as downloaded; return False if its row is gone or already downloaded.

    The row can be deleted by the ingestor while the photo is being downloaded; the file
    written for it is then removed by the daily cleanup.
    """
    result = connection.execute(
        update(NoticePhoto)
        .where(NoticePhoto.id == photo_id, NoticePhoto.downloaded_at.is_(None))
        .values(object_key=object_key, sha256=sha256, downloaded_at=now)
    )
    return result.rowcount == 1


def record_failure(connection: Connection, photo_id: int, now: datetime) -> None:
    """Count one failed download attempt."""
    connection.execute(
        update(NoticePhoto)
        .where(NoticePhoto.id == photo_id)
        .values(download_attempts=NoticePhoto.download_attempts + 1, last_attempt_at=now)
    )


def stored_object_keys(connection: Connection) -> set[str]:
    """Every object key the database refers to; files not in this set are orphans."""
    keys = connection.execute(
        select(NoticePhoto.object_key).where(NoticePhoto.object_key.is_not(None))
    ).scalars()
    return {key for key in keys if key is not None}


def next_photo_for_faces(connection: Connection) -> PhotoForFaces | None:
    """Return the next downloaded photo whose faces are not analysed yet, or None.

    Photos of removed notices are skipped, the same as for downloads.
    """
    row = connection.execute(
        select(NoticePhoto.id, NoticePhoto.object_key)
        .join(Notice, Notice.id == NoticePhoto.notice_id)
        .where(
            NoticePhoto.downloaded_at.is_not(None),
            NoticePhoto.faces_processed_at.is_(None),
            Notice.deleted_at.is_(None),
        )
        .order_by(NoticePhoto.id)
        .limit(1)
    ).first()
    if row is None or row.object_key is None:
        return None
    return PhotoForFaces(photo_id=row.id, object_key=row.object_key)


def record_faces(
    connection: Connection, photo_id: int, faces: Sequence[DetectedFace], now: datetime
) -> list[int] | None:
    """Save the faces of a photo and mark it as analysed, even if it has no face.

    Return the ids of the new faces in the order given, or None if the row is gone or
    was already analysed; nothing is written then. The caller's transaction makes both
    writes happen together or not at all.
    """
    result = connection.execute(
        update(NoticePhoto)
        .where(NoticePhoto.id == photo_id, NoticePhoto.faces_processed_at.is_(None))
        .values(faces_processed_at=now)
    )
    if result.rowcount != 1:
        return None
    if not faces:
        return []
    face_ids = connection.execute(
        insert(Face).returning(Face.id, sort_by_parameter_order=True),
        [
            {
                "photo_id": photo_id,
                "box_x": face.box_x,
                "box_y": face.box_y,
                "box_width": face.box_width,
                "box_height": face.box_height,
                "detection_score": face.detection_score,
                "embedding": face.embedding,
                "model": MODEL_NAME,
            }
            for face in faces
        ],
    ).scalars()
    return list(face_ids)


def _searched_face(connection: Connection, face_id: int) -> Row[list[float], str, int] | None:
    """Embedding, model and notice of a face, or None if it does not exist."""
    return connection.execute(
        select(Face.embedding, Face.model, NoticePhoto.notice_id)
        .join(NoticePhoto, NoticePhoto.id == Face.photo_id)
        .where(Face.id == face_id)
    ).first()


def find_similar_faces(
    connection: Connection, face_id: int, limit: int = SIMILAR_FACES_LIMIT
) -> list[SimilarFace]:
    """Return the faces of other active notices closest to a face, nearest first.

    Only faces closer than MAX_MATCH_DISTANCE and made by the same model are returned;
    an empty list if the face does not exist. Faces of the same notice are left out,
    since a notice with several photos of one person would always match itself.
    """
    target = _searched_face(connection, face_id)
    if target is None:
        return []

    # The embedding is passed as a value, not joined, so the HNSW index can be used.
    distance = Face.embedding.cosine_distance(target.embedding)
    rows = connection.execute(
        select(Face.id, Face.photo_id, NoticePhoto.notice_id, distance.label("distance"))
        .join(NoticePhoto, NoticePhoto.id == Face.photo_id)
        .join(Notice, Notice.id == NoticePhoto.notice_id)
        .where(
            NoticePhoto.notice_id != target.notice_id,
            Notice.deleted_at.is_(None),
            Face.model == target.model,
            distance < MAX_MATCH_DISTANCE,
        )
        .order_by(distance)
        .limit(limit)
    )
    return [
        SimilarFace(
            face_id=row.id,
            photo_id=row.photo_id,
            notice_id=row.notice_id,
            distance=row.distance,
        )
        for row in rows
    ]


def same_notice_faces(connection: Connection, face_id: int) -> list[SimilarFace]:
    """Return every other face of the same notice made by the same model, nearest first.

    No threshold and no limit: a notice has only a few photos, and a far face is the
    interesting case here, a photo that may not show the same person.
    """
    target = _searched_face(connection, face_id)
    if target is None:
        return []

    distance = Face.embedding.cosine_distance(target.embedding)
    rows = connection.execute(
        select(Face.id, Face.photo_id, NoticePhoto.notice_id, distance.label("distance"))
        .join(NoticePhoto, NoticePhoto.id == Face.photo_id)
        .where(
            NoticePhoto.notice_id == target.notice_id,
            Face.id != face_id,
            Face.model == target.model,
        )
        .order_by(distance, Face.id)
    )
    return [
        SimilarFace(
            face_id=row.id,
            photo_id=row.photo_id,
            notice_id=row.notice_id,
            distance=row.distance,
        )
        for row in rows
    ]


def record_matches(
    connection: Connection, face_id: int, faces: Sequence[SimilarFace], *, same_notice: bool
) -> None:
    """Save the distance between a face and each of the given faces.

    A pair is stored with the smaller id first and only once: when two new faces of one
    photo find each other, the second write is skipped.
    """
    if not faces:
        return
    connection.execute(
        pg_insert(FaceMatch)
        .values(
            [
                {
                    "first_face_id": min(face_id, face.face_id),
                    "second_face_id": max(face_id, face.face_id),
                    "distance": face.distance,
                    "same_notice": same_notice,
                }
                for face in faces
            ]
        )
        .on_conflict_do_nothing(index_elements=["first_face_id", "second_face_id"])
    )


def match_face(connection: Connection, face_id: int) -> FaceMatches:
    """Compare a face with its own notice and with other notices, and save the results."""
    same_notice = same_notice_faces(connection, face_id)
    other_notices = find_similar_faces(connection, face_id)
    record_matches(connection, face_id, same_notice, same_notice=True)
    record_matches(connection, face_id, other_notices, same_notice=False)
    return FaceMatches(face_id=face_id, same_notice=same_notice, other_notices=other_notices)


class PhotoRepository:
    """The functions above, each run in its own short transaction.

    The service holds no transaction while a photo is downloaded (ADR 0011), so every
    call commits on its own.
    """

    def __init__(self, transaction: Transaction) -> None:
        self._transaction = transaction

    def next_pending_photo(self, now: datetime) -> PendingPhoto | None:
        with self._transaction() as connection:
            return next_pending_photo(connection, now)

    def record_download(self, photo_id: int, object_key: str, sha256: str, now: datetime) -> bool:
        with self._transaction() as connection:
            return record_download(connection, photo_id, object_key, sha256, now)

    def record_failure(self, photo_id: int, now: datetime) -> None:
        with self._transaction() as connection:
            record_failure(connection, photo_id, now)

    def stored_object_keys(self) -> set[str]:
        with self._transaction() as connection:
            return stored_object_keys(connection)

    def next_photo_for_faces(self) -> PhotoForFaces | None:
        with self._transaction() as connection:
            return next_photo_for_faces(connection)

    def record_faces(
        self, photo_id: int, faces: Sequence[DetectedFace], now: datetime
    ) -> list[FaceMatches] | None:
        """Save the faces and their automatic comparisons in one transaction.

        None if the photo row is gone or was already analysed. Either everything is
        saved or nothing is, so a photo is never marked as analysed without its matches.
        """
        with self._transaction() as connection:
            face_ids = record_faces(connection, photo_id, faces, now)
            if face_ids is None:
                return None
            return [match_face(connection, face_id) for face_id in face_ids]
