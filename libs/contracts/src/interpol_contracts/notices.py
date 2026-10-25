"""Events published on the ``interpol.notices.v1`` topic (ADR 0005).

The payload mirrors Interpol's response as it was received: values are not cleaned,
converted or renamed here. Normalisation (``0`` height to ``None``, date parsing, ...)
belongs to the consumers, so that a bug in it can be fixed and the events replayed
without asking Interpol again.
"""

import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

NOTICES_TOPIC = "interpol.notices.v1"

EntityId = Annotated[str, Field(pattern=r"^\d{4}/\d+$")]


class ArrestWarrant(BaseModel):
    """One arrest warrant exactly as Interpol lists it under ``arrest_warrants``."""

    model_config = ConfigDict(frozen=True, extra="allow")
    charge: str
    issuing_country_id: str
    charge_translation: str | None = None


class NoticeDetail(BaseModel):
    """The fields of Interpol's notice detail response that the consumers read.

    Unknown fields are kept (``extra="allow"``), so nothing Interpol adds later is lost
    on the way through Kafka. Every field except ``name`` may be missing or ``null``,
    because the source is not under our control (ADR 0004).
    """

    model_config = ConfigDict(frozen=True, extra="allow")
    name: str
    forename: str | None = None
    sex_id: str | None = None
    date_of_birth: str | None = None
    place_of_birth: str | None = None
    country_of_birth_id: str | None = None
    nationalities: list[str] | None = None
    languages_spoken_ids: list[str] | None = None
    eyes_colors_id: list[str] | None = None
    hairs_id: list[str] | None = None
    height: float | None = None
    weight: float | None = None
    distinguishing_marks: str | None = None
    arrest_warrants: list[ArrestWarrant] | None = None


class NoticeSnapshot(BaseModel):
    """Payload of ``notice.observed``: the detail, its photos and their fingerprint."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    detail: NoticeDetail
    picture_ids: list[str]
    thumbnail_picture_id: str | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class _NoticeEvent(BaseModel):
    """Envelope shared by every notice event.

    Unknown fields are ignored, so that a consumer built against an older version of this
    model keeps working when a producer adds an optional field.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")
    event_id: UUID = Field(default_factory=uuid4)
    schema_version: Literal[1] = 1
    occurred_at: AwareDatetime
    scan_id: UUID
    entity_id: EntityId


class NoticeObserved(_NoticeEvent):
    """The collector fetched the notice's detail and photo list (ADR 0006)."""

    event_type: Literal["notice.observed"] = "notice.observed"
    payload: NoticeSnapshot


class NoticeSeen(_NoticeEvent):
    """The collector saw the notice during a list scan, without fetching its detail.

    Consumers use it only to refresh ``last_seen_at`` (ADR 0006).
    """

    event_type: Literal["notice.seen"] = "notice.seen"


class NoticeRemoved(_NoticeEvent):
    """The notice was missing from two consecutive complete list scans (ADR 0006)."""

    event_type: Literal["notice.removed"] = "notice.removed"


NoticeEvent = Annotated[
    NoticeObserved | NoticeSeen | NoticeRemoved, Field(discriminator="event_type")
]

_NOTICE_EVENT_ADAPTER: TypeAdapter[NoticeEvent] = TypeAdapter(NoticeEvent)


def compute_content_hash(detail: dict[str, Any], picture_ids: list[str]) -> str:
    """Return the SHA-256 of a notice's raw detail and photo list.

    The JSON is canonical (sorted keys, no whitespace) and the photo ids are sorted, so
    the same content always produces the same hash whatever the order Interpol used.
    """
    canonical = json.dumps(
        {"detail": detail, "picture_ids": sorted(picture_ids)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def serialize_event(event: NoticeEvent) -> bytes:
    """Encode an event as the UTF-8 JSON bytes written to Kafka."""
    return event.model_dump_json().encode("utf-8")


def parse_event(data: bytes) -> NoticeEvent:
    """Decode the bytes of a Kafka message into the matching event type.

    Raises ``pydantic.ValidationError`` if the bytes are not a valid notice event.
    """
    return _NOTICE_EVENT_ADAPTER.validate_json(data)


def event_key(event: NoticeEvent) -> bytes:
    """Return the Kafka message key: the entity id, so a notice keeps its partition."""
    return event.entity_id.encode("utf-8")
