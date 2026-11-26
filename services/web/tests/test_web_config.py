"""Tests of the web interface's settings."""

import pytest
from pydantic import ValidationError

from interpol_web.config import WebSettings

VARIABLES = (
    "WEB_DATABASE_URL",
    "WEB_USERNAME",
    "WEB_PASSWORD",
    "WEB_HOST",
    "WEB_PORT",
    "WEB_LOG_LEVEL",
    "WEB_LOG_FORMAT",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test without any of the variables, whatever the machine has."""
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_DATABASE_URL", "postgresql+psycopg://web:made-up@db/interpol")
    monkeypatch.setenv("WEB_USERNAME", "admin")
    monkeypatch.setenv("WEB_PASSWORD", "made-up-password")


@pytest.mark.usefixtures("required")
def test_settings_come_from_web_variables_with_defaults() -> None:
    settings = WebSettings()

    assert settings.username == "admin"
    assert settings.password.get_secret_value() == "made-up-password"
    assert (settings.host, settings.port) == ("127.0.0.1", 8000)
    assert (settings.log_level, settings.log_format) == ("INFO", "json")


@pytest.mark.usefixtures("required")
def test_secrets_never_show_in_the_repr() -> None:
    text = repr(WebSettings())

    assert "made-up" not in text


def test_the_shared_database_url_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shared URL logs in as the owner of the tables; the web interface must not
    # fall back to it when its own read-only URL is missing.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://interpol:made-up@db/interpol")
    monkeypatch.setenv("WEB_USERNAME", "admin")
    monkeypatch.setenv("WEB_PASSWORD", "made-up-password")

    with pytest.raises(ValidationError, match="database_url"):
        WebSettings()


@pytest.mark.usefixtures("required")
@pytest.mark.parametrize("port", ["0", "65536", "abc"])
def test_an_invalid_port_stops_the_start(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setenv("WEB_PORT", port)

    with pytest.raises(ValidationError, match="port"):
        WebSettings()
