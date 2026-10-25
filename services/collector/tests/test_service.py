"""Tests of the collector loop with fake scanner, details, publisher and clock."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from structlog.testing import capture_logs

from interpol_collector.detail_schedule import DetailScheduleFile
from interpol_collector.interpol_client import InterpolError, NoticeNotFoundError
from interpol_collector.list_scanner import ListScanResult
from interpol_collector.publishers import PublishError
from interpol_collector.removal_tracker import RemovalState, StateFile
from interpol_collector.service import CollectorService, Timing
from interpol_contracts import NoticeEvent

START = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
INVALID_DETAIL_ID = "2026/13"
TIMING = Timing(list_scan_interval=timedelta(hours=6), detail_refresh=timedelta(hours=12))


def scan_of(*entity_ids: str, complete: bool = True) -> ListScanResult:
    return ListScanResult(
        entity_ids=frozenset(entity_ids),
        complete=complete,
        reported_total=len(entity_ids),
        requests=1,
        age_split_requests=0,
    )


class FakeScanner:
    def __init__(self, *results: ListScanResult) -> None:
        self._results = list(results)

    def scan(self) -> ListScanResult:
        return self._results.pop(0)


class FakeDetails:
    def __init__(self, failures: dict[str, Exception] | None = None) -> None:
        self._failures = failures or {}
        self.fetched: list[str] = []

    def get_detail(self, entity_id: str) -> dict[str, Any]:
        self.fetched.append(entity_id)
        if entity_id in self._failures:
            raise self._failures[entity_id]
        return {"forename": "NO NAME"} if entity_id == INVALID_DETAIL_ID else {"name": "DOE"}

    def get_picture_ids(self, entity_id: str) -> list[str]:
        return ["100"]


class FakePublisher:
    """Delivers queued events on flush; can fail the next flush that has something to send."""

    def __init__(self) -> None:
        self.delivered: list[NoticeEvent] = []
        self._queued: list[NoticeEvent] = []
        self.fail_next_flush = False

    def publish(self, event: NoticeEvent) -> None:
        self._queued.append(event)

    def flush(self) -> None:
        queued, self._queued = self._queued, []
        if queued and self.fail_next_flush:
            self.fail_next_flush = False
            raise PublishError("simulated")
        self.delivered.extend(queued)

    def types(self) -> list[tuple[str, str]]:
        return [(event.event_type, event.entity_id) for event in self.delivered]


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def make_service(
    tmp_path: Path,
    scanner: FakeScanner,
    *,
    details: FakeDetails | None = None,
    publisher: FakePublisher | None = None,
    clock: Clock | None = None,
    sleeps: list[float] | None = None,
    max_removed_fraction: float = 1.0,
) -> tuple[CollectorService, FakePublisher, FakeDetails]:
    publisher = publisher or FakePublisher()
    details = details or FakeDetails()
    service = CollectorService(
        scanner=scanner,
        details=details,
        publisher=publisher,
        removal_file=StateFile(tmp_path / "removal_state.json"),
        schedule_file=DetailScheduleFile(tmp_path / "detail_schedule.json"),
        timing=TIMING,
        max_removed_fraction=max_removed_fraction,
        now=clock or Clock(),
        sleep=(sleeps if sleeps is not None else []).append,
        save_every=1,
    )
    return service, publisher, details


def test_first_step_scans_the_list_and_publishes_seen_events(tmp_path: Path) -> None:
    service, publisher, _ = make_service(tmp_path, FakeScanner(scan_of("2026/1", "2026/2")))

    service.step()

    assert publisher.types() == [("notice.seen", "2026/1"), ("notice.seen", "2026/2")]
    assert StateFile(tmp_path / "removal_state.json").load() == RemovalState(
        present=frozenset({"2026/1", "2026/2"})
    )


def test_steps_between_scans_refresh_details_newest_first(tmp_path: Path) -> None:
    service, publisher, details = make_service(tmp_path, FakeScanner(scan_of("2026/1", "2026/2")))

    service.step()
    service.step()
    service.step()

    assert details.fetched == ["2026/2", "2026/1"]
    observed = [event for event in publisher.delivered if event.event_type == "notice.observed"]
    assert [event.entity_id for event in observed] == ["2026/2", "2026/1"]


def test_list_is_scanned_again_after_the_interval(tmp_path: Path) -> None:
    clock = Clock()
    scanner = FakeScanner(scan_of("2026/1"), scan_of("2026/1", "2026/3"))
    service, publisher, _ = make_service(tmp_path, scanner, clock=clock)
    service.step()

    clock.now = START + timedelta(hours=6)
    service.step()

    assert ("notice.seen", "2026/3") in publisher.types()


def test_notice_missing_from_two_scans_is_removed(tmp_path: Path) -> None:
    clock = Clock()
    scanner = FakeScanner(scan_of("2026/1", "2026/2"), scan_of("2026/1"), scan_of("2026/1"))
    service, publisher, _ = make_service(tmp_path, scanner, clock=clock)

    for hours in (0, 6, 12):
        clock.now = START + timedelta(hours=hours)
        service.step()

    assert publisher.types().count(("notice.removed", "2026/2")) == 1


def test_state_is_not_saved_when_publishing_fails(tmp_path: Path) -> None:
    publisher = FakePublisher()
    publisher.fail_next_flush = True
    service, _, _ = make_service(tmp_path, FakeScanner(scan_of("2026/1")), publisher=publisher)

    with capture_logs() as logs:
        service.step()

    assert StateFile(tmp_path / "removal_state.json").load() is None
    assert any(log["event"] == "list_scan_not_published" for log in logs)


def test_missing_or_failing_detail_publishes_nothing_and_moves_on(tmp_path: Path) -> None:
    details = FakeDetails(
        failures={"2026/2": NoticeNotFoundError("gone"), "2026/1": InterpolError("down")}
    )
    service, publisher, _ = make_service(
        tmp_path, FakeScanner(scan_of("2026/1", "2026/2")), details=details
    )

    service.step()
    service.step()
    service.step()

    assert details.fetched == ["2026/2", "2026/1"]
    assert all(event.event_type == "notice.seen" for event in publisher.delivered)


def test_invalid_detail_is_rejected_without_logging_its_values(tmp_path: Path) -> None:
    service, publisher, _ = make_service(tmp_path, FakeScanner(scan_of(INVALID_DETAIL_ID)))
    service.step()

    with capture_logs() as logs:
        service.step()

    [rejected] = [log for log in logs if log["event"] == "detail_rejected"]
    assert rejected["fields"] == ["name"]
    assert "NO NAME" not in str(rejected)
    assert all(event.event_type == "notice.seen" for event in publisher.delivered)


def test_idle_step_sleeps_when_every_detail_is_fresh(tmp_path: Path) -> None:
    sleeps: list[float] = []
    service, _, _ = make_service(tmp_path, FakeScanner(scan_of("2026/1")), sleeps=sleeps)
    service.step()
    service.step()

    service.step()

    assert sleeps == [TIMING.idle_sleep.total_seconds()]


def test_undelivered_details_are_fetched_again(tmp_path: Path) -> None:
    publisher = FakePublisher()
    service, _, details = make_service(
        tmp_path, FakeScanner(scan_of("2026/1")), publisher=publisher
    )
    service.step()
    publisher.fail_next_flush = True

    service.step()
    service.step()

    assert details.fetched == ["2026/1", "2026/1"]


def test_run_stops_when_asked_and_saves_the_schedule(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path, FakeScanner(scan_of("2026/1")))
    steps = iter([False, False, True])

    service.run(should_stop=lambda: next(steps))

    saved = DetailScheduleFile(tmp_path / "detail_schedule.json").load()
    assert set(saved.last_attempts()) == {"2026/1"}


def test_saved_state_is_used_after_a_restart(tmp_path: Path) -> None:
    first, _, _ = make_service(tmp_path, FakeScanner(scan_of("2026/1", "2026/2")))
    first.step()
    first.step()
    first.step()

    second, _, details = make_service(tmp_path, FakeScanner(scan_of("2026/1", "2026/2")))
    second.step()
    second.step()

    assert details.fetched == []


def test_details_are_fetched_after_an_incomplete_first_scan(tmp_path: Path) -> None:
    service, _, details = make_service(tmp_path, FakeScanner(scan_of("2026/1", complete=False)))

    service.step()
    service.step()

    assert details.fetched == ["2026/1"]


def test_blocked_removals_are_logged_and_nothing_is_removed(tmp_path: Path) -> None:
    clock = Clock()
    scanner = FakeScanner(scan_of("2026/1", "2026/2"), scan_of("2026/1"), scan_of("2026/1"))
    service, publisher, _ = make_service(tmp_path, scanner, clock=clock, max_removed_fraction=0.1)

    with capture_logs() as logs:
        for hours in (0, 6, 12):
            clock.now = START + timedelta(hours=hours)
            service.step()

    assert ("notice.removed", "2026/2") not in publisher.types()
    assert any(log["event"] == "removals_blocked" for log in logs)
