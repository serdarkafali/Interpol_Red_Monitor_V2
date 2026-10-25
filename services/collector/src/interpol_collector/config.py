"""Configuration for the collector service, loaded from environment variables."""

from pathlib import Path
from typing import Self

from pydantic import AliasChoices, Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from interpol_observability import LogFormat, LogLevel


class CollectorSettings(BaseSettings):
    """Runtime settings of the collector service.

    Values are read from environment variables prefixed with ``COLLECTOR_`` and
    validated once at startup. No file is read: locally, pass the ``.env`` file with
    ``uv run --env-file .env``; in Docker, Compose sets the variables.
    """

    model_config = SettingsConfigDict(env_prefix="COLLECTOR_", extra="ignore")

    interpol_api_base_url: HttpUrl = HttpUrl("https://ws-public.interpol.int/notices/v1/red")
    request_delay_min_seconds: float = Field(default=5.0, ge=0)
    request_delay_max_seconds: float = Field(default=8.0, ge=0)
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        validation_alias=AliasChoices(
            "COLLECTOR_KAFKA_BOOTSTRAP_SERVERS", "KAFKA_BOOTSTRAP_SERVERS"
        ),
    )
    dry_run: bool = True
    state_dir: Path = Path("var/collector")
    list_scan_interval_hours: float = Field(default=6.0, gt=0)
    detail_refresh_hours: float = Field(default=12.0, ge=0)
    max_removed_fraction: float = Field(default=0.05, ge=0, le=1)
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    @model_validator(mode="after")
    def _validate_delay_range(self) -> Self:
        if self.request_delay_min_seconds > self.request_delay_max_seconds:
            msg = (
                f"request_delay_min_seconds ({self.request_delay_min_seconds}) must not exceed "
                f"request_delay_max_seconds ({self.request_delay_max_seconds})"
            )
            raise ValueError(msg)
        return self
