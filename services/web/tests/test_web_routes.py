"""Tests of the pages with a made-up search: no database is needed."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from interpol_web.app import create_app
from interpol_web.config import WebSettings
from interpol_web.filters import MAX_PAGE, PAGE_SIZE, NoticeFilters
from interpol_web.queries import FacetOption, NoticeCard, SearchResult
from interpol_web.routes import page_count, page_url

AUTH = ("admin", "made-up-password")


class RecordingSearch:
    """Remembers the filters it was asked with and answers with a fixed total."""

    def __init__(self, total: int = 0) -> None:
        self.total = total
        self.asked: list[NoticeFilters] = []

    def search(self, filters: NoticeFilters) -> SearchResult:
        self.asked.append(filters)
        return SearchResult(cards=(), total=self.total, sexes=(), nationalities=(), issuers=())


def client_for(search: RecordingSearch) -> TestClient:
    settings = WebSettings(
        database_url=SecretStr("postgresql+psycopg://web:made-up@db/interpol"),
        username=AUTH[0],
        password=SecretStr(AUTH[1]),
    )
    return TestClient(create_app(settings, search))


def test_the_filters_of_the_url_reach_the_search() -> None:
    search = RecordingSearch()

    response = client_for(search).get(
        "/?sex=F&nationality=RU&nationality=DE&age_min=30&page=2", auth=AUTH
    )

    assert response.status_code == 200
    assert search.asked == [NoticeFilters(sex=["F"], nationality=["RU", "DE"], age_min=30, page=2)]


def test_the_page_shows_the_total() -> None:
    response = client_for(RecordingSearch(total=1284)).get("/", auth=AUTH)

    assert "1.284" in response.text


def test_a_wrong_filter_is_refused_before_the_search() -> None:
    search = RecordingSearch()

    response = client_for(search).get("/?sex=X", auth=AUTH)

    assert response.status_code == 422
    assert search.asked == []


def test_without_login_the_search_is_not_asked() -> None:
    search = RecordingSearch()

    assert client_for(search).get("/").status_code == 401
    assert search.asked == []


@pytest.mark.parametrize(
    ("total", "pages"),
    [(0, 1), (1, 1), (PAGE_SIZE, 1), (PAGE_SIZE + 1, 2), (PAGE_SIZE * 10_000, MAX_PAGE)],
)
def test_page_count(total: int, pages: int) -> None:
    assert page_count(total) == pages


def test_a_page_link_keeps_the_filters_and_changes_only_the_page() -> None:
    filters = NoticeFilters(sex=["F"], nationality=["RU", "DE"], has_photo=True, page=2)

    assert page_url(filters, 3) == "/?sex=F&nationality=RU&nationality=DE&has_photo=true&page=3"


def test_default_filters_are_left_out_of_a_page_link() -> None:
    assert page_url(NoticeFilters(), 1) == "/"
    assert page_url(NoticeFilters(), 2) == "/?page=2"


def test_a_page_link_reads_back_as_the_same_filters() -> None:
    search = RecordingSearch()
    filters = NoticeFilters(q="doe", issuer=["US"], has_match=True, sort="name")

    client_for(search).get(page_url(filters, 5), auth=AUTH)

    assert search.asked == [filters.model_copy(update={"page": 5})]


def a_result() -> SearchResult:
    """A made-up page: one card and a few sidebar options."""
    return SearchResult(
        cards=(
            NoticeCard(
                id=1,
                entity_id="2026/111",
                name="DOE",
                forename="JOHN",
                sex="M",
                age=36,
                nationalities=("DE", "RU"),
                photo_id=None,
            ),
        ),
        total=30,
        sexes=(FacetOption("M", "M", 20), FacetOption("F", "F", 10), FacetOption("U", "U", 0)),
        nationalities=(FacetOption("RU", "Russia", 12), FacetOption("DE", "Germany", 8)),
        issuers=(),
    )


class FixedSearch(RecordingSearch):
    def search(self, filters: NoticeFilters) -> SearchResult:
        self.asked.append(filters)
        return a_result()


def test_the_page_shows_cards_counts_and_the_ticked_options() -> None:
    response = client_for(FixedSearch()).get("/?nationality=RU&age_min=30&q=doe", auth=AUTH)
    html = response.text

    assert "JOHN DOE" in html
    assert "2026/111" in html
    assert "Russia" in html
    assert 'name="nationality" value="RU"\n                   checked' in html
    assert 'name="nationality" value="DE"\n                   >' in html
    assert 'value="30"' in html
    assert 'value="doe"' in html


def test_page_numbers_keep_the_filters() -> None:
    html = client_for(FixedSearch()).get("/?nationality=RU", auth=AUTH).text

    assert 'href="/?nationality=RU&amp;page=2"' in html
