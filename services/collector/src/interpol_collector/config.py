"""Configuration for the collector service, loaded from environment variables."""

from typing import Self

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from interpol_observability import LogFormat, LogLevel


class CollectorSettings(BaseSettings):
    """Runtime settings of the collector service.

    Values are read from environment variables prefixed with ``COLLECTOR_``
    (or from a local ``.env`` file) and validated once at startup.
    """

    model_config = SettingsConfigDict(
        env_prefix="COLLECTOR_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    interpol_api_base_url: HttpUrl = HttpUrl("https://ws-public.interpol.int/notices/v1/red")
    page_size: int = Field(default=20, ge=1, le=160)
    request_delay_min_seconds: float = Field(default=5.0, ge=0)
    request_delay_max_seconds: float = Field(default=8.0, ge=0)
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
