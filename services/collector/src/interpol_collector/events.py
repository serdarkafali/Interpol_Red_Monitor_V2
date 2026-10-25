"""Turns what the collector read from Interpol into the events of ADR 0005 and 0006.

This is the only place in the collector that builds ``interpol_contracts`` events, so
the mapping from scans and details to messages can be read and tested in one file.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from interpol_contracts import (
    NoticeDetail,
    NoticeObserved,
    NoticeRemoved,
    NoticeSeen,
    NoticeSnapshot,
    compute_content_hash,
)


def seen_events(
    scan_id: UUID, entity_ids: Iterable[str], occurred_at: datetime
) -> list[NoticeSeen]:
    """Return one ``notice.seen`` per id, in a stable order."""
    return [
        NoticeSeen(scan_id=scan_id, entity_id=entity_id, occurred_at=occurred_at)
        for entity_id in sorted(entity_ids)
    ]


def removed_events(
    scan_id: UUID, entity_ids: Iterable[str], occurred_at: datetime
) -> list[NoticeRemoved]:
    """Return one ``notice.removed`` per id, in a stable order."""
    return [
        NoticeRemoved(scan_id=scan_id, entity_id=entity_id, occurred_at=occurred_at)
        for entity_id in sorted(entity_ids)
    ]


def observed_event(
    scan_id: UUID,
    entity_id: str,
    detail: dict[str, Any],
    picture_ids: list[str],
    occurred_at: datetime,
) -> NoticeObserved:
    """Return the ``notice.observed`` event carrying a raw detail and its photo ids.

    Raises ``pydantic.ValidationError`` if the detail does not match ``NoticeDetail``,
    for example when Interpol drops a field the consumers rely on.
    """
    snapshot = NoticeSnapshot(
        detail=NoticeDetail.model_validate(detail),
        picture_ids=picture_ids,
        content_hash=compute_content_hash(detail, picture_ids),
    )
    return NoticeObserved(
        scan_id=scan_id, entity_id=entity_id, occurred_at=occurred_at, payload=snapshot
    )
