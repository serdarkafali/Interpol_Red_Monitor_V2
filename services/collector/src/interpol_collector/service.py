"""The collector's main loop: list scans on a timer, detail fetches in between (ADR 0006).

The loop runs one step at a time, so a shutdown request is honoured between steps.
State files are saved only after the events they describe were flushed to Kafka; a
crash therefore repeats events (at-least-once) instead of losing them.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from interpol_collector.detail_schedule import DetailScheduleFile
from interpol_collector.events import observed_event, removed_events, seen_events
from interpol_collector.interpol_client import InterpolError, NoticeNotFoundError
from interpol_collector.list_scanner import ListScanResult
from interpol_collector.publishers import EventPublisher, PublishError
from interpol_collector.removal_tracker import StateFile, decide_removals
from interpol_observability import get_logger

logger = get_logger(__name__)


class Scanner(Protocol):
    """Runs one list scan; the ListScanner fits."""

    def scan(self) -> ListScanResult: ...


class DetailSource(Protocol):
    """Fetches one notice's detail and photo list; the Interpol client fits."""

    def get_detail(self, entity_id: str) -> dict[str, Any]: ...

    def get_picture_ids(self, entity_id: str) -> list[str]: ...


@dataclass(frozen=True)
class Timing:
    """How often the loop does what."""

    list_scan_interval: timedelta
    detail_refresh: timedelta
    idle_sleep: timedelta = timedelta(minutes=1)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CollectorService:
    """Decides, step by step, whether to scan the list or refresh a detail."""

    def __init__(
        self,
        *,
        scanner: Scanner,
        details: DetailSource,
        publisher: EventPublisher,
        removal_file: StateFile,
        schedule_file: DetailScheduleFile,
        timing: Timing,
        max_removed_fraction: float,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], object] = time.sleep,
        save_every: int = 20,
    ) -> None:
        self._scanner = scanner
        self._details = details
        self._publisher = publisher
        self._removal_file = removal_file
        self._schedule_file = schedule_file
        self._timing = timing
        self._max_removed_fraction = max_removed_fraction
        self._now = now
        self._sleep = sleep
        self._save_every = save_every

        self._removal_state = removal_file.load()
        self._present = self._removal_state.present if self._removal_state else frozenset()
        self._schedule = schedule_file.load()
        self._schedule.sync(self._present)
        self._last_scan_at: datetime | None = None
        self._scan_id: UUID = uuid4()
        self._unsaved_attempts = 0

    def run(self, should_stop: Callable[[], bool]) -> None:
        """Run steps until ``should_stop`` returns True, then save what is pending."""
        try:
            while not should_stop():
                self.step()
        finally:
            self._save_schedule()

    def step(self) -> None:
        """Do one unit of work: a list scan if one is due, otherwise one detail."""
        now = self._now()
        if (
            self._last_scan_at is None
            or now - self._last_scan_at >= self._timing.list_scan_interval
        ):
            self._run_list_scan(now)
            return
        entity_id = self._schedule.next_due(now, self._timing.detail_refresh)
        if entity_id is None:
            self._sleep(self._timing.idle_sleep.total_seconds())
            return
        self._refresh_detail(entity_id)

    def _run_list_scan(self, started_at: datetime) -> None:
        self._save_schedule()
        scan_id = uuid4()
        result = self._scanner.scan()
        self._last_scan_at = started_at
        decision = decide_removals(
            self._removal_state, result, max_removed_fraction=self._max_removed_fraction
        )
        if decision.blocked:
            logger.error("removals_blocked", scan_id=str(scan_id), count=len(decision.blocked))

        finished_at = self._now()
        try:
            for event in seen_events(scan_id, result.entity_ids, finished_at):
                self._publisher.publish(event)
            for removed in removed_events(scan_id, decision.removed, finished_at):
                self._publisher.publish(removed)
            self._publisher.flush()
        except (PublishError, ValidationError) as error:
            # Nothing is saved, so the next scan decides the same removals again.
            logger.error(
                "list_scan_not_published", scan_id=str(scan_id), error=type(error).__name__
            )
            self._forget_unsaved_attempts()
            return

        self._removal_file.save(decision.state)
        self._removal_state = decision.state
        self._present = decision.state.present | result.entity_ids
        self._schedule.sync(self._present)
        self._scan_id = scan_id
        logger.info(
            "list_scan_processed",
            scan_id=str(scan_id),
            complete=result.complete,
            seen=len(result.entity_ids),
            removed=len(decision.removed),
            blocked=len(decision.blocked),
        )

    def _refresh_detail(self, entity_id: str) -> None:
        try:
            detail = self._details.get_detail(entity_id)
            picture_ids = self._details.get_picture_ids(entity_id)
        except NoticeNotFoundError:
            logger.info("detail_not_found", entity_id=entity_id)
        except InterpolError:
            logger.warning("detail_fetch_failed", entity_id=entity_id, exc_info=True)
        else:
            self._publish_observed(entity_id, detail, picture_ids)

        self._schedule.record_attempt(entity_id, self._now())
        self._unsaved_attempts += 1
        if self._unsaved_attempts >= self._save_every:
            self._save_schedule()

    def _publish_observed(
        self, entity_id: str, detail: dict[str, Any], picture_ids: list[str]
    ) -> None:
        try:
            event = observed_event(self._scan_id, entity_id, detail, picture_ids, self._now())
        except ValidationError as error:
            # Only the field paths are logged; the values may be personal data.
            fields = [".".join(str(part) for part in issue["loc"]) for issue in error.errors()]
            logger.error("detail_rejected", entity_id=entity_id, fields=fields)
            return
        self._publisher.publish(event)

    def _save_schedule(self) -> None:
        try:
            self._publisher.flush()
        except PublishError:
            logger.error("detail_events_not_published", attempts=self._unsaved_attempts)
            self._forget_unsaved_attempts()
            return
        self._schedule_file.save(self._schedule)
        self._unsaved_attempts = 0

    def _forget_unsaved_attempts(self) -> None:
        """Go back to the last saved schedule, so undelivered details are fetched again."""
        self._schedule = self._schedule_file.load()
        self._schedule.sync(self._present)
        self._unsaved_attempts = 0
