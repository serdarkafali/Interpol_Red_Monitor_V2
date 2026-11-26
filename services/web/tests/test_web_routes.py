"""Tests of the pages with a made-up reader: no database is needed."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from interpol_web.app import create_app
from interpol_web.config import WebSettings
from interpol_web.filters import MAX_PAGE, PAGE_SIZE, NoticeFilters
from interpol_web.queries import (
    CountryName,
    FaceBox,
    FacetOption,
    NoticeCard,
    NoticeDetail,
    PhotoFaces,
    PossibleMatch,
    SearchResult,
    Warrant,
)
from interpol_web.routes import page_count, page_url

AUTH = ("admin", "made-up-password")


class RecordingSearch:
    """Remembers the filters it was asked with and answers with a fixed total."""

    def __init__(self, total: int = 0, detail: NoticeDetail | None = None) -> None:
        self.total = total
        self.detail = detail
        self.asked: list[NoticeFilters] = []
        self.asked_ids: list[int] = []

    def notice(self, notice_id: int) -> NoticeDetail | None:
        self.asked_ids.append(notice_id)
        return self.detail

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


def a_detail() -> NoticeDetail:
    """A made-up notice with one warrant, one photo with one face and one possible match."""
    seen = datetime(2026, 11, 1, tzinfo=UTC)
    return NoticeDetail(
        id=7,
        entity_id="2026/777",
        name="DOE",
        forename="JOHN",
        sex="M",
        date_of_birth=date(1990, 1, 1),
        age=36,
        place_of_birth="Testville",
        country_of_birth=CountryName("DE", "Germany"),
        height=Decimal("1.80"),
        weight=80,
        distinguishing_marks=None,
        languages=("ENG",),
        eye_colors=("BRO",),
        hair_colors=(),
        first_seen_at=seen,
        last_seen_at=seen,
        removed_at=None,
        nationalities=(CountryName("DE", "Germany"),),
        warrants=(Warrant(CountryName("US", "United States"), "made-up charge", None),),
        photos=(PhotoFaces(photo_id=11, faces=(FaceBox(21, 10, 20, 30, 40, 0.95),)),),
        matches=(
            PossibleMatch(
                face_id=21,
                other_face_id=31,
                other_photo_id=12,
                other_notice_id=8,
                other_entity_id="2026/888",
                other_name="ROE",
                other_forename="JANE",
                other_removed=True,
                similarity=0.634,
            ),
        ),
    )


def test_the_detail_page_shows_the_notice() -> None:
    search = RecordingSearch(detail=a_detail())

    response = client_for(search).get("/notices/7", auth=AUTH)
    html = response.text

    assert response.status_code == 200
    assert search.asked_ids == [7]
    assert "JOHN DOE" in html
    assert "made-up charge" in html
    assert 'src="/photos/11"' in html
    assert 'data-x="10" data-y="20"' in html
    assert 'href="http://testserver/notices/8"' in html
    assert "%63" in html


def test_an_unknown_notice_is_not_found() -> None:
    assert client_for(RecordingSearch()).get("/notices/999", auth=AUTH).status_code == 404


@pytest.mark.parametrize("notice_id", ["0", "-1", "abc", str(2**63)])
def test_a_wrong_notice_id_is_refused_before_the_reader(notice_id: str) -> None:
    search = RecordingSearch(detail=a_detail())

    response = client_for(search).get(f"/notices/{notice_id}", auth=AUTH)

    assert response.status_code == 422
    assert search.asked_ids == []


def test_a_card_links_to_its_detail_page() -> None:
    html = client_for(FixedSearch()).get("/", auth=AUTH).text

    assert 'href="http://testserver/notices/1"' in html
