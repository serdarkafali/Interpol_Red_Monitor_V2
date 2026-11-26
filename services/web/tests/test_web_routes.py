"""Tests of the pages with a made-up reader: no database is needed."""

from dataclasses import replace
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
    MatchPage,
    MatchPair,
    MatchSide,
    NoticeCard,
    NoticeDetail,
    OwnComparison,
    PhotoFaces,
    PossibleMatch,
    SearchResult,
    Warrant,
)
from interpol_web.routes import page_count, page_url

AUTH = ("admin", "made-up-password")
FAKE_JPEG = b"\xff\xd8\xff made-up photo"


class RecordingSearch:
    """Remembers the filters it was asked with and answers with a fixed total."""

    def __init__(self, total: int = 0, detail: NoticeDetail | None = None) -> None:
        self.total = total
        self.detail = detail
        self.asked: list[NoticeFilters] = []
        self.asked_ids: list[int] = []
        self.asked_pages: list[int] = []

    def search(self, filters: NoticeFilters) -> SearchResult:
        self.asked.append(filters)
        return SearchResult(cards=(), total=self.total, sexes=(), nationalities=(), issuers=())

    def notice(self, notice_id: int) -> NoticeDetail | None:
        self.asked_ids.append(notice_id)
        return self.detail

    def photo_key(self, photo_id: int) -> str | None:
        """Photo 11 is downloaded, every other id is unknown."""
        return "notices/2026-777/pic" if photo_id == 11 else None

    def matches(self, page: int) -> MatchPage:
        self.asked_pages.append(page)
        return a_match_page()


class FakePhotos:
    """Holds one made-up photo; remembers which keys it was asked for."""

    def __init__(self, stored: dict[str, bytes] | None = None) -> None:
        self.stored = stored if stored is not None else {"notices/2026-777/pic": FAKE_JPEG}
        self.asked: list[str] = []

    def get(self, key: str) -> bytes | None:
        self.asked.append(key)
        return self.stored.get(key)


def client_for(search: RecordingSearch, photos: FakePhotos | None = None) -> TestClient:
    settings = WebSettings(
        database_url=SecretStr("postgresql+psycopg://web:made-up@db/interpol"),
        username=AUTH[0],
        password=SecretStr(AUTH[1]),
        WEB_STORAGE_ACCESS_KEY="made-up-key",
        WEB_STORAGE_SECRET_KEY="made-up-secret",
    )
    return TestClient(create_app(settings, search, photos or FakePhotos()))


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
        own_comparisons=(
            OwnComparison(first_photo_id=11, second_photo_id=13, similarity=0.21),
            OwnComparison(first_photo_id=11, second_photo_id=11, similarity=0.88),
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
    assert 'src="http://testserver/photos/11"' in html
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


def test_a_photo_is_handed_out_as_jpeg() -> None:
    photos = FakePhotos()

    response = client_for(RecordingSearch(), photos).get("/photos/11", auth=AUTH)

    assert response.status_code == 200
    assert response.content == FAKE_JPEG
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert photos.asked == ["notices/2026-777/pic"]


def test_an_unknown_photo_is_not_found_and_the_store_is_not_asked() -> None:
    photos = FakePhotos()

    response = client_for(RecordingSearch(), photos).get("/photos/12", auth=AUTH)

    assert response.status_code == 404
    assert photos.asked == []


def test_a_photo_missing_from_the_store_is_not_found() -> None:
    response = client_for(RecordingSearch(), FakePhotos(stored={})).get("/photos/11", auth=AUTH)

    assert response.status_code == 404


def test_a_photo_needs_the_login_too() -> None:
    photos = FakePhotos()

    assert client_for(RecordingSearch(), photos).get("/photos/11").status_code == 401
    assert photos.asked == []


class PhotoCardSearch(RecordingSearch):
    """Answers with one card whose photo is downloaded."""

    def search(self, filters: NoticeFilters) -> SearchResult:
        result = a_result()
        return replace(result, cards=(replace(result.cards[0], photo_id=11),))


def test_a_card_with_a_photo_shows_it() -> None:
    html = client_for(PhotoCardSearch()).get("/", auth=AUTH).text

    assert '<img src="http://testserver/photos/11"' in html


def a_match_page() -> MatchPage:
    """A made-up page with one pair of 30, so there are two pages."""

    def side(notice_id: int, entity_id: str, name: str, *, removed: bool = False) -> MatchSide:
        return MatchSide(
            notice_id=notice_id,
            entity_id=entity_id,
            name=name,
            forename=None,
            removed=removed,
            photo_id=notice_id * 10,
            box=FaceBox(notice_id * 100, 1, 2, 3, 4, 0.9),
        )

    pair = MatchPair(
        first=side(1, "2026/111", "DOE"),
        second=side(2, "2026/222", "ROE", removed=True),
        similarity=0.61,
    )
    return MatchPage(pairs=(pair,), total=30)


def test_the_match_page_shows_pairs_side_by_side() -> None:
    search = RecordingSearch()

    response = client_for(search).get("/matches", auth=AUTH)
    html = response.text

    assert response.status_code == 200
    assert search.asked_pages == [1]
    assert "DOE" in html
    assert "ROE" in html
    assert "%61" in html
    assert 'src="http://testserver/photos/10"' in html
    assert 'data-face-id="200"' in html
    assert 'href="http://testserver/notices/2"' in html
    assert 'href="?page=2"' in html


@pytest.mark.parametrize("page", ["0", "-1", "abc", str(MAX_PAGE + 1)])
def test_a_wrong_match_page_is_refused(page: str) -> None:
    search = RecordingSearch()

    assert client_for(search).get(f"/matches?page={page}", auth=AUTH).status_code == 422
    assert search.asked_pages == []


def test_the_menu_links_to_the_match_page() -> None:
    html = client_for(RecordingSearch()).get("/", auth=AUTH).text

    assert 'href="http://testserver/matches"' in html


def test_own_photos_that_may_differ_are_marked() -> None:
    html = client_for(RecordingSearch(detail=a_detail())).get("/notices/7", auth=AUTH).text

    assert "%21" in html
    assert html.count("badge warn") == 1
