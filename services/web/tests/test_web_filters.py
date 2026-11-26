"""Tests of the filter bar's choices, as FastAPI reads them from the URL."""

from typing import Annotated

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from interpol_web.filters import MAX_AGE, PAGE_SIZE, NoticeFilters

# A tiny app that only reads the filters and returns them, so the tests see exactly what
# FastAPI builds from a URL.
app = FastAPI()


@app.get("/")
def read(filters: Annotated[NoticeFilters, Query()]) -> NoticeFilters:
    return filters


client = TestClient(app)


def filters_for(url: str) -> dict[str, object]:
    response = client.get(url)
    assert response.status_code == 200, response.text
    result: dict[str, object] = response.json()
    return result


def test_no_parameters_mean_active_notices_newest_first() -> None:
    assert filters_for("/") == {
        "q": None,
        "sex": [],
        "nationality": [],
        "issuer": [],
        "age_min": None,
        "age_max": None,
        "has_photo": False,
        "has_match": False,
        "status": "active",
        "sort": "newest",
        "page": 1,
    }


def test_a_repeated_parameter_becomes_a_list() -> None:
    result = filters_for("/?sex=M&sex=F&nationality=TR&nationality=DE&issuer=RU")

    assert result["sex"] == ["M", "F"]
    assert result["nationality"] == ["TR", "DE"]
    assert result["issuer"] == ["RU"]


def test_a_submitted_form_with_empty_boxes_is_accepted() -> None:
    # What the browser sends when the text and number boxes are left empty and a
    # checkbox is ticked.
    result = filters_for("/?q=&age_min=&age_max=&has_photo=on")

    assert (result["q"], result["age_min"], result["age_max"]) == (None, None, None)
    assert result["has_photo"] is True


def test_the_search_text_is_trimmed() -> None:
    assert filters_for("/?q=%20%20doe%20")["q"] == "doe"


def test_ages_given_the_wrong_way_round_are_swapped() -> None:
    result = filters_for("/?age_min=50&age_max=30")

    assert (result["age_min"], result["age_max"]) == (30, 50)


@pytest.mark.parametrize(
    "query",
    [
        "sex=X",
        "nationality=tr",
        "nationality=TUR",
        "issuer=<script>",
        f"age_min={MAX_AGE + 1}",
        "age_max=-1",
        "age_min=old",
        "status=deleted",
        "sort=random",
        "page=0",
        "page=1001",
        "q=" + "a" * 101,
    ],
)
def test_invalid_values_are_refused(query: str) -> None:
    assert client.get(f"/?{query}").status_code == 422


def test_an_unknown_parameter_is_refused() -> None:
    # A typo must not look like a working filter.
    assert client.get("/?natinality=TR").status_code == 422


@pytest.mark.parametrize(("page", "offset"), [(1, 0), (2, PAGE_SIZE), (3, 2 * PAGE_SIZE)])
def test_offset_skips_the_notices_of_the_pages_before(page: int, offset: int) -> None:
    assert NoticeFilters(page=page).offset == offset
