"""Tests of the detail refresh order and of the schedule file."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from interpol_collector.detail_schedule import DetailSchedule, DetailScheduleFile

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
REFRESH = timedelta(hours=12)


def test_new_notices_come_first_newest_id_first() -> None:
    schedule = DetailSchedule({"2026/1": NOW - timedelta(days=3)})
    schedule.sync({"2026/1", "2025/900", "2026/20"})

    assert schedule.next_due(NOW, REFRESH) == "2026/20"
    schedule.record_attempt("2026/20", NOW)
    assert schedule.next_due(NOW, REFRESH) == "2025/900"


def test_oldest_attempt_comes_next_once_every_notice_was_tried() -> None:
    schedule = DetailSchedule(
        {"2026/1": NOW - timedelta(hours=20), "2026/2": NOW - timedelta(hours=30)}
    )
    schedule.sync({"2026/1", "2026/2"})

    assert schedule.next_due(NOW, REFRESH) == "2026/2"


def test_nothing_is_due_while_every_detail_is_fresh() -> None:
    schedule = DetailSchedule({"2026/1": NOW - timedelta(hours=1)})
    schedule.sync({"2026/1"})

    assert schedule.next_due(NOW, REFRESH) is None


def test_empty_schedule_has_nothing_due() -> None:
    assert DetailSchedule().next_due(NOW, REFRESH) is None


def test_sync_forgets_notices_that_left_the_list() -> None:
    schedule = DetailSchedule({"2026/1": NOW, "2026/2": NOW})

    schedule.sync({"2026/1"})

    assert schedule.last_attempts() == {"2026/1": NOW}


def test_attempt_of_an_unknown_notice_is_ignored() -> None:
    schedule = DetailSchedule()
    schedule.sync({"2026/1"})

    schedule.record_attempt("2026/9", NOW)

    assert schedule.last_attempts() == {}


def test_malformed_id_does_not_break_the_order() -> None:
    schedule = DetailSchedule()
    schedule.sync({"not-an-id", "2026/1"})

    assert schedule.next_due(NOW, REFRESH) == "2026/1"


def test_schedule_survives_a_save_and_load(tmp_path: Path) -> None:
    schedule_file = DetailScheduleFile(tmp_path / "schedule.json")
    schedule = DetailSchedule({"2026/1": NOW})

    schedule_file.save(schedule)

    assert schedule_file.load().last_attempts() == {"2026/1": NOW}


def test_missing_or_unreadable_schedule_loads_empty(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    assert DetailScheduleFile(path).load().last_attempts() == {}

    path.write_text("[]", encoding="utf-8")

    assert DetailScheduleFile(path).load().last_attempts() == {}
