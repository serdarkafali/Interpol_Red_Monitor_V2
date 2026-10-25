"""Tests of the atomic file write."""

from pathlib import Path

from interpol_collector.state_io import write_text_atomically


def test_write_creates_missing_directories(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "state.json"

    write_text_atomically(path, "{}")

    assert path.read_text(encoding="utf-8") == "{}"


def test_write_replaces_the_file_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    write_text_atomically(path, "old")

    write_text_atomically(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    assert [child.name for child in tmp_path.iterdir()] == ["state.json"]
