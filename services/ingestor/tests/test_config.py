"""Tests for IngestorSettings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from interpol_ingestor.config import IngestorSettings

URL = "postgresql+psycopg://user:secret@localhost:5432/db"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "INGESTOR_DATABASE_URL", "KAFKA_BOOTSTRAP_SERVERS"):
        monkeypatch.delenv(name, raising=False)


def test_the_database_url_is_required() -> None:
    with pytest.raises(ValidationError):
        IngestorSettings()


def test_the_shared_database_url_is_accepted_and_kept_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)

    settings = IngestorSettings()

    assert settings.database_url.get_secret_value() == URL
    assert "secret" not in repr(settings)


def test_the_ingestor_variables_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("INGESTOR_DATABASE_URL", "postgresql+psycopg://other")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:19092")
    monkeypatch.setenv("INGESTOR_KAFKA_BOOTSTRAP_SERVERS", "localhost:9999")

    settings = IngestorSettings()

    assert settings.database_url.get_secret_value() == "postgresql+psycopg://other"
    assert settings.kafka_bootstrap_servers == "localhost:9999"


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)

    settings = IngestorSettings()

    assert (settings.consumer_group, settings.ignore_content_hash) == ("ingestor", False)


def test_no_env_file_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(f"DATABASE_URL={URL}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError):
        IngestorSettings()
