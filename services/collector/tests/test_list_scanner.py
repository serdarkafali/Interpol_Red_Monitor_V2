"""Tests of the list scanner against an in-memory imitation of the Interpol list."""

from collections.abc import Mapping
from dataclasses import dataclass

from structlog.testing import capture_logs

from interpol_collector.interpol_client import PAGE_SIZE, InterpolError, ListPage
from interpol_collector.list_scanner import ListScanner, iso_country_codes

COUNTRIES = ("DE", "RU", "TR")


@dataclass(frozen=True)
class FakeNotice:
    entity_id: str
    sex: str
    nationalities: tuple[str, ...]
    issuers: tuple[str, ...]
    age: int | None


class FakeLister:
    """Answers list queries like Interpol: filters the notices, returns at most 160."""

    def __init__(
        self, notices: list[FakeNotice], failing: list[dict[str, str | int]] | None = None
    ) -> None:
        self._notices = notices
        self._failing = failing or []
        self.requests: list[dict[str, str | int]] = []

    def list_notices(self, filters: Mapping[str, str | int]) -> ListPage:
        query = dict(filters)
        self.requests.append(query)
        if query in self._failing:
            raise InterpolError("simulated failure")
        matches = [notice for notice in self._notices if _matches(notice, query)]
        ids = tuple(notice.entity_id for notice in matches[:PAGE_SIZE])
        return ListPage(total=len(matches), entity_ids=ids)


def _matches(notice: FakeNotice, query: dict[str, str | int]) -> bool:
    if "sexId" in query and notice.sex != query["sexId"]:
        return False
    if "nationality" in query and query["nationality"] not in notice.nationalities:
        return False
    if "arrestWarrantCountryId" in query and query["arrestWarrantCountryId"] not in notice.issuers:
        return False
    if "ageMin" in query or "ageMax" in query:
        if notice.age is None:
            return False
        if not int(query.get("ageMin", 0)) <= notice.age <= int(query.get("ageMax", 120)):
            return False
    return True


def make_notices(
    count: int,
    *,
    sex: str = "M",
    nationalities: tuple[str, ...] = ("TR",),
    issuers: tuple[str, ...] | None = None,
    age: int | None = 30,
    first_id: int = 1,
) -> list[FakeNotice]:
    """Make notices; unless given, the warrants are issued by the first nationality."""
    if issuers is None:
        issuers = nationalities[:1] or ("TR",)
    return [
        FakeNotice(f"2026/{number}", sex, nationalities, issuers, age)
        for number in range(first_id, first_id + count)
    ]


def ids_of(notices: list[FakeNotice]) -> frozenset[str]:
    return frozenset(notice.entity_id for notice in notices)


def test_small_list_is_read_with_one_request() -> None:
    notices = make_notices(100)
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert result.entity_ids == ids_of(notices)
    assert result.complete
    assert result.reported_total == 100
    assert lister.requests == [{}]


def test_large_list_is_split_by_sex() -> None:
    notices = make_notices(150, sex="M") + make_notices(100, sex="F", first_id=151)
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert result.entity_ids == ids_of(notices)
    assert result.complete
    assert lister.requests == [{}, {"sexId": "M"}, {"sexId": "F"}, {"sexId": "U"}]


def test_large_sex_group_is_split_by_issuing_country() -> None:
    notices = make_notices(120, nationalities=("TR",)) + make_notices(
        80, nationalities=("RU",), first_id=121
    )
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert result.entity_ids == ids_of(notices)
    assert result.complete
    assert {"sexId": "M", "arrestWarrantCountryId": "TR"} in lister.requests
    assert {"sexId": "M", "arrestWarrantCountryId": "DE"} in lister.requests


def test_notice_with_warrants_from_two_countries_is_collected_once() -> None:
    notices = (
        make_notices(90, issuers=("TR", "RU"))
        + make_notices(50, issuers=("TR",), first_id=91)
        + make_notices(30, issuers=("RU",), first_id=141)
    )
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert len(result.entity_ids) == 170
    assert result.complete


def test_large_issuing_country_group_is_split_by_halving_the_age_range() -> None:
    notices = [
        notice
        for age in range(20, 40)
        for notice in make_notices(10, nationalities=("RU",), age=age, first_id=age * 100)
    ]
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert result.entity_ids == ids_of(notices)
    assert result.complete
    rus = {"sexId": "M", "arrestWarrantCountryId": "RU"}
    assert {**rus, "ageMin": 0, "ageMax": 60} in lister.requests
    assert {**rus, "ageMin": 61, "ageMax": 120} in lister.requests
    assert result.age_split_requests > 0


def test_group_that_cannot_be_split_makes_the_scan_incomplete() -> None:
    notices = make_notices(170, nationalities=("RU",), age=40)
    lister = FakeLister(notices)

    with capture_logs() as logs:
        result = ListScanner(lister, COUNTRIES).scan()

    assert not result.complete
    assert any(log["event"] == "list_group_cannot_be_split" for log in logs)


def test_notice_without_birth_date_is_seen_when_its_group_is_small() -> None:
    no_birth_date = make_notices(1, age=None)
    notices = (
        no_birth_date
        + make_notices(100, sex="F", nationalities=("TR",), first_id=2)
        + make_notices(70, sex="F", nationalities=("RU",), first_id=102)
    )
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert ids_of(no_birth_date) <= result.entity_ids
    assert result.complete


def test_age_split_that_loses_notices_is_logged() -> None:
    notices = make_notices(5, nationalities=("RU",), age=None) + [
        notice
        for age in range(20, 37)
        for notice in make_notices(10, nationalities=("RU",), age=age, first_id=age * 100)
    ]
    lister = FakeLister(notices)

    with capture_logs() as logs:
        ListScanner(lister, COUNTRIES).scan()

    differences = [log for log in logs if log["event"] == "list_split_totals_differ"]
    assert differences
    assert differences[0]["total"] == 175
    assert differences[0]["children_total"] == 170


def test_failed_query_makes_the_scan_incomplete_but_keeps_what_was_seen() -> None:
    males = make_notices(150, sex="M")
    notices = males + make_notices(100, sex="F", first_id=151)
    lister = FakeLister(notices, failing=[{"sexId": "F"}])

    result = ListScanner(lister, COUNTRIES).scan()

    assert not result.complete
    assert ids_of(males) <= result.entity_ids


def test_failed_first_query_returns_an_empty_incomplete_scan() -> None:
    lister = FakeLister(make_notices(10), failing=[{}])

    result = ListScanner(lister, COUNTRIES).scan()

    assert result.entity_ids == frozenset()
    assert not result.complete
    assert result.reported_total == 0


def test_result_counts_every_request() -> None:
    notices = make_notices(150, sex="M") + make_notices(100, sex="F", first_id=151)
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert result.requests == len(lister.requests)
    assert result.age_split_requests == 0


def test_iso_country_codes_are_two_letter_codes() -> None:
    codes = iso_country_codes()

    assert "TR" in codes
    assert len(codes) > 240
    assert all(len(code) == 2 and code.isupper() for code in codes)


def test_scan_stops_sending_requests_when_asked_to_stop() -> None:
    notices = make_notices(150, sex="M") + make_notices(100, sex="F", first_id=151)
    lister = FakeLister(notices)
    stop_after = 2

    result = ListScanner(
        lister, COUNTRIES, should_stop=lambda: len(lister.requests) >= stop_after
    ).scan()

    assert len(lister.requests) == stop_after
    assert not result.complete


def test_single_age_above_the_limit_is_split_by_nationality() -> None:
    russians = make_notices(160, nationalities=("RU",), age=37)
    others = make_notices(2, nationalities=("DE",), issuers=("RU",), age=37, first_id=200)
    lister = FakeLister(russians + others)

    result = ListScanner(lister, COUNTRIES).scan()

    assert result.entity_ids == ids_of(russians + others)
    assert result.complete
    leaf = {"sexId": "M", "arrestWarrantCountryId": "RU", "ageMin": 37, "ageMax": 37}
    assert {**leaf, "nationality": "RU"} in lister.requests


def test_notice_without_nationality_is_found_through_its_warrant() -> None:
    stateless = make_notices(1, nationalities=(), issuers=("DE",))
    notices = stateless + make_notices(170, first_id=2)
    lister = FakeLister(notices)

    result = ListScanner(lister, COUNTRIES).scan()

    assert ids_of(stateless) <= result.entity_ids
