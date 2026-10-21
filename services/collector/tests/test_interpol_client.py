"""Tests of the Interpol client. No request ever reaches the network."""

from collections.abc import Mapping
from typing import Any

import pytest

from interpol_collector.interpol_client import (
    InterpolClient,
    InterpolError,
    ListPage,
    NoticeNotFoundError,
    TransportResponse,
)

BASE_URL = "https://example.test/notices/v1/red"


class FakeTransport:
    """Returns prepared responses in order and records every request it receives."""

    def __init__(self, *responses: TransportResponse | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, dict[str, str | int]]] = []

    def get(self, url: str, params: Mapping[str, str | int]) -> TransportResponse:
        self.requests.append((url, dict(params)))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(body: dict[str, Any]) -> TransportResponse:
    return TransportResponse(status_code=200, body=body)


def make_client(transport: FakeTransport, sleeps: list[float]) -> InterpolClient:
    return InterpolClient(transport, BASE_URL, delay_range=(5.0, 8.0), sleep=sleeps.append)


LIST_BODY = {
    "total": 2,
    "_embedded": {"notices": [{"entity_id": "2026/1"}, {"entity_id": "2026/2"}]},
}


def test_list_notices_asks_for_one_full_page_and_returns_ids() -> None:
    transport = FakeTransport(ok(LIST_BODY))

    page = make_client(transport, []).list_notices({"sexId": "M", "nationality": "TR"})

    assert page == ListPage(total=2, entity_ids=("2026/1", "2026/2"))
    assert transport.requests == [
        (BASE_URL, {"sexId": "M", "nationality": "TR", "resultPerPage": 160, "page": 1})
    ]


def test_unknown_filter_is_refused_before_any_request() -> None:
    transport = FakeTransport()

    with pytest.raises(ValueError, match="sexID"):
        make_client(transport, []).list_notices({"sexID": "M"})

    assert transport.requests == []


def test_detail_and_images_use_the_url_form_of_the_id() -> None:
    images = {"_embedded": {"images": [{"picture_id": "100"}, {"picture_id": 101}]}}
    transport = FakeTransport(ok({"name": "DOE"}), ok(images))
    client = make_client(transport, [])

    detail = client.get_detail("2026/7")
    picture_ids = client.get_picture_ids("2026/7")

    assert detail == {"name": "DOE"}
    assert picture_ids == ["100", "101"]
    assert [url for url, _ in transport.requests] == [
        f"{BASE_URL}/2026-7",
        f"{BASE_URL}/2026-7/images",
    ]


def test_every_request_is_followed_by_a_polite_delay() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(ok(LIST_BODY), ok({"name": "DOE"}))
    client = make_client(transport, sleeps)

    client.list_notices({})
    client.get_detail("2026/1")

    assert len(sleeps) == 2
    assert all(5.0 <= seconds <= 8.0 for seconds in sleeps)


@pytest.mark.parametrize(
    "failure",
    [TransportResponse(status_code=503, body=None), ConnectionError("network down")],
)
def test_temporary_failures_are_retried(failure: TransportResponse | Exception) -> None:
    transport = FakeTransport(failure, ok({"name": "DOE"}))

    detail = make_client(transport, []).get_detail("2026/1")

    assert detail == {"name": "DOE"}
    assert len(transport.requests) == 2


def test_client_gives_up_after_the_last_attempt() -> None:
    failure = TransportResponse(status_code=503, body=None)
    transport = FakeTransport(failure, failure, failure)

    with pytest.raises(InterpolError, match="3 attempts"):
        make_client(transport, []).get_detail("2026/1")

    assert len(transport.requests) == 3


def test_not_found_is_not_retried() -> None:
    transport = FakeTransport(TransportResponse(status_code=404, body=None))

    with pytest.raises(NoticeNotFoundError):
        make_client(transport, []).get_detail("2026/1")

    assert len(transport.requests) == 1


def test_other_client_errors_are_not_retried() -> None:
    transport = FakeTransport(TransportResponse(status_code=400, body=None))

    with pytest.raises(InterpolError, match="400"):
        make_client(transport, []).get_detail("2026/1")

    assert len(transport.requests) == 1


def test_list_request_accepts_every_allowed_filter() -> None:
    transport = FakeTransport(ok(LIST_BODY))

    make_client(transport, []).list_notices(
        {"sexId": "M", "nationality": "RU", "ageMin": 40, "ageMax": 40}
    )

    assert len(transport.requests) == 1


def test_empty_list_response_has_no_ids() -> None:
    transport = FakeTransport(ok({"total": 0}))

    page = make_client(transport, []).list_notices({"nationality": "VA"})

    assert page == ListPage(total=0, entity_ids=())


@pytest.mark.parametrize(
    "body",
    [
        {"_embedded": {"notices": []}},
        {"total": -1},
        {"total": 1, "_embedded": {"notices": [{"name": "DOE"}]}},
    ],
)
def test_malformed_list_response_is_an_interpol_error(body: dict[str, Any]) -> None:
    transport = FakeTransport(ok(body))

    with pytest.raises(InterpolError, match="unexpected list response"):
        make_client(transport, []).list_notices({})


def test_malformed_image_response_is_an_interpol_error() -> None:
    transport = FakeTransport(ok({"_embedded": {"images": [{"name": "front"}]}}))

    with pytest.raises(InterpolError, match="unexpected image response"):
        make_client(transport, []).get_picture_ids("2026/1")
