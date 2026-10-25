"""Chooses whose detail the collector fetches next, and remembers it between runs.

New notices come first, then the ones attempted longest ago (ADR 0006). An attempt is
recorded whether it succeeded or not, so a notice that keeps failing does not block the
others; it simply waits for its turn again.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ValidationError

from interpol_collector.state_io import write_text_atomically
from interpol_observability import get_logger

logger = get_logger(__name__)


class DetailSchedule:
    """The notices on the list and when their detail was last attempted."""

    def __init__(self, last_attempts: Mapping[str, datetime] | None = None) -> None:
        self._last_attempts = dict(last_attempts or {})
        self._present: frozenset[str] = frozenset(self._last_attempts)

    def sync(self, present: Iterable[str]) -> None:
        """Track exactly the notices in ``present``; forget the ones that left the list."""
        self._present = frozenset(present)
        self._last_attempts = {
            entity_id: attempted_at
            for entity_id, attempted_at in self._last_attempts.items()
            if entity_id in self._present
        }

    def next_due(self, now: datetime, min_refresh: timedelta) -> str | None:
        """Return the notice to fetch next, or None if every detail is fresh enough."""
        never_attempted = self._present - self._last_attempts.keys()
        if never_attempted:
            return max(never_attempted, key=_newest_first)
        if not self._last_attempts:
            return None
        entity_id, attempted_at = min(self._last_attempts.items(), key=lambda item: item[1])
        if now - attempted_at < min_refresh:
            return None
        return entity_id

    def record_attempt(self, entity_id: str, attempted_at: datetime) -> None:
        """Remember that the detail of ``entity_id`` was just attempted."""
        if entity_id in self._present:
            self._last_attempts[entity_id] = attempted_at

    def last_attempts(self) -> dict[str, datetime]:
        """Return a copy of the attempt times, for saving."""
        return dict(self._last_attempts)


def _newest_first(entity_id: str) -> tuple[int, int]:
    """Sort key for ids like ``2026/123``: higher year and number means newer."""
    year, _, number = entity_id.partition("/")
    try:
        return int(year), int(number)
    except ValueError:
        return 0, 0


class _ScheduleDocument(BaseModel):
    """On-disk format of the schedule file."""

    version: Literal[1]
    last_attempts: dict[str, AwareDatetime]


class DetailScheduleFile:
    """Loads and saves the attempt times of a DetailSchedule as JSON."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> DetailSchedule:
        """Return the saved schedule, or an empty one if it is missing or unreadable."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.info("detail_schedule_not_found", path=str(self._path))
            return DetailSchedule()
        try:
            document = _ScheduleDocument.model_validate_json(text)
        except ValidationError:
            logger.error("detail_schedule_unreadable", path=str(self._path), exc_info=True)
            return DetailSchedule()
        return DetailSchedule(document.last_attempts)

    def save(self, schedule: DetailSchedule) -> None:
        """Write the schedule atomically."""
        document = _ScheduleDocument(version=1, last_attempts=schedule.last_attempts())
        write_text_atomically(self._path, document.model_dump_json())
