"""Tests of the two-scan removal rule and of the state file."""

from pathlib import Path

from structlog.testing import capture_logs

from interpol_collector.list_scanner import ListScanResult
from interpol_collector.removal_tracker import (
    RemovalState,
    StateFile,
    decide_removals,
)


def scan_of(*entity_ids: str, complete: bool = True) -> ListScanResult:
    return ListScanResult(
        entity_ids=frozenset(entity_ids),
        complete=complete,
        reported_total=len(entity_ids),
        requests=1,
        age_split_requests=0,
    )


def state_of(present: set[str], missing_once: set[str] | None = None) -> RemovalState:
    return RemovalState(present=frozenset(present), missing_once=frozenset(missing_once or set()))


def test_first_complete_scan_only_records_what_it_saw() -> None:
    decision = decide_removals(None, scan_of("2026/1", "2026/2"))

    assert decision.state == state_of({"2026/1", "2026/2"})
    assert decision.removed == frozenset()


def test_notice_missing_from_one_scan_is_not_removed_yet() -> None:
    decision = decide_removals(state_of({"2026/1", "2026/2"}), scan_of("2026/1"))

    assert decision.removed == frozenset()
    assert decision.state == state_of({"2026/1", "2026/2"}, missing_once={"2026/2"})


def test_notice_missing_from_two_scans_in_a_row_is_removed() -> None:
    state = state_of({"2026/1", "2026/2"}, missing_once={"2026/2"})

    decision = decide_removals(state, scan_of("2026/1"), max_removed_fraction=1.0)

    assert decision.removed == frozenset({"2026/2"})
    assert decision.state == state_of({"2026/1"})


def test_notice_that_comes_back_loses_its_warning() -> None:
    state = state_of({"2026/1", "2026/2"}, missing_once={"2026/2"})

    decision = decide_removals(state, scan_of("2026/1", "2026/2"))

    assert decision.removed == frozenset()
    assert decision.state == state_of({"2026/1", "2026/2"})


def test_new_notice_is_added_to_the_present_notices() -> None:
    decision = decide_removals(state_of({"2026/1"}), scan_of("2026/1", "2026/9"))

    assert decision.state == state_of({"2026/1", "2026/9"})


def test_incomplete_scan_changes_nothing() -> None:
    state = state_of({"2026/1", "2026/2"}, missing_once={"2026/2"})

    decision = decide_removals(state, scan_of(complete=False))

    assert decision.state == state
    assert decision.removed == frozenset()


def test_incomplete_first_scan_leaves_an_empty_state() -> None:
    decision = decide_removals(None, scan_of("2026/1", complete=False))

    assert decision.state == RemovalState()
    assert decision.removed == frozenset()


def test_removing_too_many_notices_at_once_is_blocked() -> None:
    present = {f"2026/{number}" for number in range(1, 101)}
    state = state_of(present, missing_once=present)

    decision = decide_removals(state, scan_of("2026/1"), max_removed_fraction=0.05)

    assert decision.removed == frozenset()
    assert len(decision.blocked) == 99
    assert decision.state == state


def test_missing_state_file_loads_as_none(tmp_path: Path) -> None:
    assert StateFile(tmp_path / "state.json").load() is None


def test_state_survives_a_save_and_load(tmp_path: Path) -> None:
    state_file = StateFile(tmp_path / "nested" / "state.json")
    state = state_of({"2026/1", "2026/2"}, missing_once={"2026/2"})

    state_file.save(state)

    assert state_file.load() == state
    assert [path.name for path in (tmp_path / "nested").iterdir()] == ["state.json"]


def test_save_replaces_the_previous_state(tmp_path: Path) -> None:
    state_file = StateFile(tmp_path / "state.json")
    state_file.save(state_of({"2026/1"}))

    state_file.save(state_of({"2026/2"}))

    assert state_file.load() == state_of({"2026/2"})


def test_unreadable_state_file_loads_as_none_and_is_logged(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    with capture_logs() as logs:
        loaded = StateFile(path).load()

    assert loaded is None
    assert any(log["event"] == "removal_state_unreadable" for log in logs)


def test_state_file_with_unknown_version_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"version": 2, "present": [], "missing_once": []}', encoding="utf-8")

    assert StateFile(path).load() is None
