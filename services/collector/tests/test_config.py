"""Tests for CollectorSettings."""

import pytest
from pydantic import ValidationError

from interpol_collector.config import CollectorSettings


def test_defaults_are_valid() -> None:
    settings = CollectorSettings(_env_file=None)

    assert settings.page_size == 20
    assert settings.log_level == "INFO"


def test_environment_variable_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_PAGE_SIZE", "50")

    settings = CollectorSettings(_env_file=None)

    assert settings.page_size == 50


def test_min_delay_greater_than_max_delay_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_REQUEST_DELAY_MIN_SECONDS", "10")
    monkeypatch.setenv("COLLECTOR_REQUEST_DELAY_MAX_SECONDS", "2")

    with pytest.raises(ValidationError, match="must not exceed"):
        CollectorSettings(_env_file=None)


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError):
        CollectorSettings(_env_file=None)


@pytest.mark.parametrize("page_size", ["0", "-5", "161"])
def test_out_of_range_page_size_is_rejected(
    monkeypatch: pytest.MonkeyPatch, page_size: str
) -> None:
    monkeypatch.setenv("COLLECTOR_PAGE_SIZE", page_size)

    with pytest.raises(ValidationError):
        CollectorSettings(_env_file=None)
