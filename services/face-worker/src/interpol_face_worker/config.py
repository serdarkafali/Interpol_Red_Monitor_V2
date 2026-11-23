"""Configuration for the face worker, loaded from environment variables."""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from interpol_observability import LogFormat, LogLevel


class FaceWorkerSettings(BaseSettings):
    """Runtime settings of the face worker.

    Values are read from environment variables prefixed with ``FACE_WORKER_`` and
    validated once at startup. No file is read: locally, pass the ``.env`` file with
    ``uv run --env-file .env``; in Docker, Compose sets the variables.
    """

    model_config = SettingsConfigDict(env_prefix="FACE_WORKER_", extra="ignore")

    # Secrets: never shown in a log or repr.
    database_url: SecretStr = Field(
        validation_alias=AliasChoices("FACE_WORKER_DATABASE_URL", "DATABASE_URL")
    )
    storage_access_key: SecretStr = Field(
        validation_alias=AliasChoices("FACE_WORKER_STORAGE_ACCESS_KEY", "STORAGE_ACCESS_KEY")
    )
    storage_secret_key: SecretStr = Field(
        validation_alias=AliasChoices("FACE_WORKER_STORAGE_SECRET_KEY", "STORAGE_SECRET_KEY")
    )
    storage_endpoint_url: str = "http://localhost:8333"
    storage_bucket: str = "notice-photos"
    # Pointed at a fake server for end-to-end runs, so no real photo is downloaded.
    interpol_base_url: str = "https://ws-public.interpol.int/notices/v1/red"
    idle_seconds: float = Field(default=60.0, gt=0)
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
