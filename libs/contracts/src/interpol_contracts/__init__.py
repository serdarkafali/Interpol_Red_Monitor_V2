"""Message contracts shared by the services that talk over Kafka.

Producers and consumers import the same models, so both sides always agree on the format.
Only data definitions live here; no business logic.
"""

from interpol_contracts.notices import (
    NOTICES_TOPIC,
    ArrestWarrant,
    NoticeDetail,
    NoticeEvent,
    NoticeObserved,
    NoticeRemoved,
    NoticeSeen,
    NoticeSnapshot,
    compute_content_hash,
    event_key,
    parse_event,
    serialize_event,
)

__all__ = [
    "NOTICES_TOPIC",
    "ArrestWarrant",
    "NoticeDetail",
    "NoticeEvent",
    "NoticeObserved",
    "NoticeRemoved",
    "NoticeSeen",
    "NoticeSnapshot",
    "compute_content_hash",
    "event_key",
    "parse_event",
    "serialize_event",
]
