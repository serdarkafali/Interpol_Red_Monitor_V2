"""Tests for CollectorSettings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from interpol_collector.config import CollectorSettings


def test_defaults_are_valid() -> None:
    settings = CollectorSettings()

    assert settings.request_delay_min_seconds == 5.0
    assert settings.log_level == "INFO"


def test_dry_run_is_the_default() -> None:
    assert CollectorSettings().dry_run is True


def test_shared_kafka_variable_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:19092")

    assert CollectorSettings().kafka_bootstrap_servers == "kafka:19092"


def test_collector_kafka_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:19092")
    monkeypatch.setenv("COLLECTOR_KAFKA_BOOTSTRAP_SERVERS", "localhost:9999")

    assert CollectorSettings().kafka_bootstrap_servers == "localhost:9999"


def test_no_env_file_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("COLLECTOR_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert CollectorSettings().log_level == "INFO"


def test_environment_variable_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_LOG_LEVEL", "DEBUG")

    settings = CollectorSettings()

    assert settings.log_level == "DEBUG"


def test_min_delay_greater_than_max_delay_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_REQUEST_DELAY_MIN_SECONDS", "10")
    monkeypatch.setenv("COLLECTOR_REQUEST_DELAY_MAX_SECONDS", "2")

    with pytest.raises(ValidationError, match="must not exceed"):
        CollectorSettings()


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError):
        CollectorSettings()
