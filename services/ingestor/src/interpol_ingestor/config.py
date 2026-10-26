"""Configuration for the ingestor service, loaded from environment variables."""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from interpol_observability import LogFormat, LogLevel


class IngestorSettings(BaseSettings):
    """Runtime settings of the ingestor service.

    Values are read from environment variables prefixed with ``INGESTOR_`` and validated
    once at startup. No file is read: locally, pass the ``.env`` file with
    ``uv run --env-file .env``; in Docker, Compose sets the variables.
    """

    model_config = SettingsConfigDict(env_prefix="INGESTOR_", extra="ignore")

    # Secret: the URL holds the database password and must never appear in a log or repr.
    database_url: SecretStr = Field(
        validation_alias=AliasChoices("INGESTOR_DATABASE_URL", "DATABASE_URL")
    )
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        validation_alias=AliasChoices(
            "INGESTOR_KAFKA_BOOTSTRAP_SERVERS", "KAFKA_BOOTSTRAP_SERVERS"
        ),
    )
    consumer_group: str = "ingestor"
    # Only for replaying events after a normalisation fix (ADR 0008).
    ignore_content_hash: bool = False
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
