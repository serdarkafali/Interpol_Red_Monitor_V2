"""Tests for FaceWorkerSettings.

Not called test_config.py: the collector already has a test module of that name and
mypy names these test modules by their file names.
"""

import pytest
from pydantic import ValidationError

from interpol_face_worker.config import FaceWorkerSettings

URL = "postgresql+psycopg://user:secret@localhost:5432/db"
NAMES = (
    "DATABASE_URL",
    "STORAGE_ACCESS_KEY",
    "STORAGE_SECRET_KEY",
    "FACE_WORKER_DATABASE_URL",
    "FACE_WORKER_STORAGE_ACCESS_KEY",
    "FACE_WORKER_STORAGE_SECRET_KEY",
    "FACE_WORKER_INTERPOL_BASE_URL",
    "FACE_WORKER_IDLE_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def shared_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("STORAGE_ACCESS_KEY", "access-key-value")
    monkeypatch.setenv("STORAGE_SECRET_KEY", "secret-key-value")


def test_the_database_url_and_storage_keys_are_required() -> None:
    with pytest.raises(ValidationError) as caught:
        FaceWorkerSettings()

    # Errors are reported under the first accepted variable name.
    missing = {error["loc"][0] for error in caught.value.errors()}
    assert missing == {
        "FACE_WORKER_DATABASE_URL",
        "FACE_WORKER_STORAGE_ACCESS_KEY",
        "FACE_WORKER_STORAGE_SECRET_KEY",
    }


@pytest.mark.usefixtures("shared_variables")
def test_the_shared_variables_are_accepted_and_kept_secret() -> None:
    settings = FaceWorkerSettings()

    assert settings.database_url.get_secret_value() == URL
    assert settings.storage_secret_key.get_secret_value() == "secret-key-value"
    for secret in ("secret@", "access-key-value", "secret-key-value"):
        assert secret not in repr(settings)


@pytest.mark.usefixtures("shared_variables")
def test_defaults_point_at_interpol_and_the_photo_bucket() -> None:
    settings = FaceWorkerSettings()

    assert settings.interpol_base_url == "https://ws-public.interpol.int/notices/v1/red"
    assert settings.storage_bucket == "notice-photos"
    assert settings.idle_seconds == 60.0


@pytest.mark.usefixtures("shared_variables")
def test_the_face_worker_variables_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACE_WORKER_STORAGE_ACCESS_KEY", "own-key")
    monkeypatch.setenv("FACE_WORKER_INTERPOL_BASE_URL", "http://fake-interpol:8080/red")

    settings = FaceWorkerSettings()

    assert settings.storage_access_key.get_secret_value() == "own-key"
    assert settings.interpol_base_url == "http://fake-interpol:8080/red"


@pytest.mark.usefixtures("shared_variables")
def test_a_non_positive_idle_time_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACE_WORKER_IDLE_SECONDS", "0")

    with pytest.raises(ValidationError):
        FaceWorkerSettings()
