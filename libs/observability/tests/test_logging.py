"""Tests for the shared logging setup."""

import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from interpol_observability import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def _read_json_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    output = capsys.readouterr().out
    return [json.loads(line) for line in output.splitlines() if line]


def test_json_log_contains_standard_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service_name="test-service")

    get_logger("my.module").info("notice_fetched", interpol_id="2026/123")

    (record,) = _read_json_lines(capsys)
    assert record["event"] == "notice_fetched"
    assert record["interpol_id"] == "2026/123"
    assert record["level"] == "info"
    assert record["service"] == "test-service"
    assert record["logger"] == "my.module"
    assert "timestamp" in record


def test_standard_library_logs_use_the_same_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service_name="test-service")

    logging.getLogger("third.party").warning("connection retry")

    (record,) = _read_json_lines(capsys)
    assert record["event"] == "connection retry"
    assert record["level"] == "warning"
    assert record["service"] == "test-service"


def test_records_below_configured_level_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service_name="test-service", level="WARNING")

    logger = get_logger(__name__)
    logger.info("should_not_appear")
    logger.warning("should_appear")

    records = _read_json_lines(capsys)
    assert [r["event"] for r in records] == ["should_appear"]


def test_context_variables_are_attached(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(service_name="test-service")

    with structlog.contextvars.bound_contextvars(run_id="run-42"):
        get_logger(__name__).info("page_fetched", page=3)

    (record,) = _read_json_lines(capsys)
    assert record["run_id"] == "run-42"
    assert record["page"] == 3
