"""Tests of the conversion from Interpol's raw detail to a database record."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from interpol_contracts import NoticeSnapshot, compute_content_hash
from interpol_ingestor.normalize import (
    NormalizedNotice,
    RejectedNoticeError,
    normalize_notice,
    optional_text,
)

# Made-up person; every value is invented for the tests.
DETAIL: dict[str, Any] = {
    "name": "TESTER",
    "forename": "SAMPLE",
    "sex_id": "M",
    "date_of_birth": "1980/05/17",
    "place_of_birth": "Exampletown",
    "country_of_birth_id": "DE",
    "nationalities": ["DE", "FR"],
    "languages_spoken_ids": ["GER"],
    "eyes_colors_id": ["BRO"],
    "hairs_id": None,
    "height": 1.8,
    "weight": 72.4,
    "distinguishing_marks": None,
    "arrest_warrants": [
        {"issuing_country_id": "NL", "charge": "Made-up charge", "charge_translation": ""}
    ],
}


def normalize(*, picture_ids: list[str] | None = None, **changes: Any) -> NormalizedNotice:
    detail = {**DETAIL, **changes}
    pictures = picture_ids or ["101"]
    snapshot = NoticeSnapshot.model_validate(
        {
            "detail": detail,
            "picture_ids": pictures,
            "content_hash": compute_content_hash(detail, pictures),
        }
    )
    return normalize_notice("2026/1", snapshot)


def test_a_complete_detail_is_converted_to_schema_types() -> None:
    result = normalize()
    record = result.record

    assert result.issues == ()
    assert record.entity_id == "2026/1"
    assert record.date_of_birth == date(1980, 5, 17)
    assert record.height == Decimal("1.80")
    assert record.weight == 72
    assert record.nationality_codes == ("DE", "FR")
    assert record.hair_color_codes == ()
    assert record.arrest_warrants[0].charge_translation is None
    assert record.country_codes == {"DE", "FR", "NL"}


@pytest.mark.parametrize("field", ["height", "weight"])
def test_zero_means_unknown_and_is_not_an_issue(field: str) -> None:
    unknown: dict[str, Any] = {field: 0}
    result = normalize(**unknown)

    assert getattr(result.record, field) is None
    assert result.issues == ()


@pytest.mark.parametrize(
    ("changes", "field", "issue"),
    [
        ({"height": 3.2}, "height", "height_out_of_range"),
        ({"height": -1.7}, "height", "height_out_of_range"),
        ({"weight": 500}, "weight", "weight_out_of_range"),
        ({"date_of_birth": "1980"}, "date_of_birth", "date_of_birth_unparsable"),
        ({"date_of_birth": "1980/13/01"}, "date_of_birth", "date_of_birth_unparsable"),
        ({"sex_id": "X"}, "sex_id", "sex_id_unknown"),
        ({"country_of_birth_id": "DEU"}, "country_of_birth_code", "country_of_birth_code_invalid"),
    ],
)
def test_a_value_that_does_not_fit_becomes_none_with_an_issue(
    changes: dict[str, Any], field: str, issue: str
) -> None:
    result = normalize(**changes)

    assert getattr(result.record, field) is None
    assert result.issues == (issue,)


def test_values_are_rounded_to_the_column_precision() -> None:
    record = normalize(height=1.755, weight=72.5).record

    assert record.height == Decimal("1.76")
    assert record.weight == 73


def test_invalid_and_repeated_nationalities_are_dropped() -> None:
    result = normalize(nationalities=["DE", "de", "DE", "XKX", "FR"])

    assert result.record.nationality_codes == ("DE", "FR")
    assert result.issues == ("nationality_code_invalid",)


def test_repeated_picture_ids_are_stored_once() -> None:
    assert normalize(picture_ids=["2", "1", "2"]).record.picture_ids == ("2", "1")


def test_missing_lists_become_empty() -> None:
    record = normalize(nationalities=None, arrest_warrants=None, languages_spoken_ids=None).record

    assert record.nationality_codes == ()
    assert record.arrest_warrants == ()
    assert record.language_codes == ()


@pytest.mark.parametrize(
    ("warrant", "reason"),
    [
        ({"issuing_country_id": "N1", "charge": "x"}, "arrest_warrant_country_invalid"),
        ({"issuing_country_id": "NL", "charge": "  "}, "arrest_warrant_charge_empty"),
    ],
)
def test_a_broken_arrest_warrant_rejects_the_notice(warrant: dict[str, Any], reason: str) -> None:
    with pytest.raises(RejectedNoticeError) as excinfo:
        normalize(arrest_warrants=[warrant])

    assert excinfo.value.reason == reason


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), (" ", None), ("a", "a")])
def test_optional_text(value: str | None, expected: str | None) -> None:
    assert optional_text(value) == expected
