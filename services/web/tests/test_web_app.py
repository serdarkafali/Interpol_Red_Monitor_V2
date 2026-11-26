"""Tests of the application shell: login, cache header, static files, no API docs."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from interpol_web.app import create_app
from interpol_web.config import WebSettings
from interpol_web.filters import NoticeFilters
from interpol_web.queries import MatchPage, NoticeDetail, SearchResult

USER = "admin"
PASSWORD = "made-up-password"


class EmptyReader:
    """A reader that finds nothing; these tests are about the shell, not the data."""

    def search(self, filters: NoticeFilters) -> SearchResult:
        return SearchResult(cards=(), total=0, sexes=(), nationalities=(), issuers=())

    def notice(self, notice_id: int) -> NoticeDetail | None:
        return None

    def photo_key(self, photo_id: int) -> str | None:
        return None

    def matches(self, page: int) -> MatchPage:
        return MatchPage(pairs=(), total=0)


class NoPhotos:
    def get(self, key: str) -> bytes | None:
        return None


@pytest.fixture
def client() -> TestClient:
    settings = WebSettings(
        database_url=SecretStr("postgresql+psycopg://web:made-up@db/interpol"),
        username=USER,
        password=SecretStr(PASSWORD),
        WEB_STORAGE_ACCESS_KEY="made-up-key",
        WEB_STORAGE_SECRET_KEY="made-up-secret",
    )
    return TestClient(create_app(settings, EmptyReader(), NoPhotos()))


def test_a_page_without_credentials_asks_the_browser_to_log_in(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Basic"


@pytest.mark.parametrize(
    ("user", "password"),
    [(USER, "wrong"), ("someone", PASSWORD), ("", "")],
)
def test_wrong_credentials_are_refused(client: TestClient, user: str, password: str) -> None:
    assert client.get("/", auth=(user, password)).status_code == 401


def test_the_right_credentials_show_the_page_with_the_sidebar(client: TestClient) -> None:
    response = client.get("/", auth=(USER, PASSWORD))

    assert response.status_code == 200
    assert 'class="sidebar"' in response.text
    assert "Filtrele" in response.text


@pytest.mark.parametrize("credentials", [None, (USER, PASSWORD)])
def test_no_answer_may_be_kept_by_the_browser(
    client: TestClient, credentials: tuple[str, str] | None
) -> None:
    response = client.get("/", auth=credentials)

    assert response.headers["Cache-Control"] == "no-store"


def test_the_stylesheet_is_served(client: TestClient) -> None:
    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert ".sidebar" in response.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_there_is_no_api_documentation(client: TestClient, path: str) -> None:
    assert client.get(path, auth=(USER, PASSWORD)).status_code == 404


def test_the_health_check_needs_no_login_and_shows_no_data(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.text == "ok"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/", "/matches", "/notices/1", "/photos/1"])
def test_every_page_and_photo_asks_for_the_login(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401
