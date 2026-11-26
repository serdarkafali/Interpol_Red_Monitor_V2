"""Configuration for the web interface, loaded from environment variables."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from interpol_observability import LogFormat, LogLevel


class WebSettings(BaseSettings):
    """Runtime settings of the web interface (ADR 0013).

    Values are read from environment variables prefixed with ``WEB_`` and validated once
    at startup. No file is read: locally, pass the ``.env`` file with
    ``uv run --env-file .env``; in Docker, Compose sets the variables.

    Unlike the other services there is no fallback to the shared ``DATABASE_URL``: that
    one logs in as the owner of the tables, and the web interface must only ever use its
    own read-only user.
    """

    model_config = SettingsConfigDict(env_prefix="WEB_", extra="ignore")

    # Secrets: never shown in a log or repr.
    # Logs in as the read-only "web" user (ADR 0013), never as the owner of the tables.
    database_url: SecretStr
    # The user name and password asked by the browser before any page is shown.
    username: str
    password: SecretStr

    # 127.0.0.1: reachable only from this computer. Docker sets 0.0.0.0 inside the
    # container, and Compose publishes the port on 127.0.0.1 of the host only.
    host: str = "127.0.0.1"
    port: int = Field(default=8000, gt=0, lt=65536)
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
