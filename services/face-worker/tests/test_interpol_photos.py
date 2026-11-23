"""Tests of the photo client. No request ever reaches the network."""

import pytest

from interpol_face_worker.interpol_photos import (
    JPEG_SIGNATURE,
    MAX_PHOTO_BYTES,
    PhotoClient,
    PhotoDownloadError,
    TransportResponse,
    to_url_id,
)

BASE_URL = "https://example.test/notices/v1/red"
PHOTO_URL = f"{BASE_URL}/2026-7/images/111"
# Made-up bytes that only start like a JPEG; no real photo is used in tests.
FAKE_JPEG = JPEG_SIGNATURE + b"made-up photo"


class FakeTransport:
    """Returns prepared responses in order and records every URL it receives."""

    def __init__(self, *responses: TransportResponse | Exception) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str) -> TransportResponse:
        self.urls.append(url)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def answer(status_code: int, content: bytes = b"") -> TransportResponse:
    return TransportResponse(status_code=status_code, content=content)


def make_client(transport: FakeTransport, sleeps: list[float]) -> PhotoClient:
    return PhotoClient(transport, BASE_URL + "/", delay_range=(5.0, 8.0), sleep=sleeps.append)


def download_error(transport: FakeTransport) -> PhotoDownloadError:
    with pytest.raises(PhotoDownloadError) as caught:
        make_client(transport, []).download("2026/7", "111")
    return caught.value


def test_to_url_id_replaces_the_slash() -> None:
    assert to_url_id("2026/7") == "2026-7"


def test_download_returns_the_photo_and_waits_after_the_request() -> None:
    transport = FakeTransport(answer(200, FAKE_JPEG))
    sleeps: list[float] = []

    content = make_client(transport, sleeps).download("2026/7", "111")

    assert content == FAKE_JPEG
    # The trailing slash of the base URL does not produce "//".
    assert transport.urls == [PHOTO_URL]
    assert len(sleeps) == 1
    assert 5.0 <= sleeps[0] <= 8.0


def test_temporary_failures_are_retried_with_growing_backoff() -> None:
    transport = FakeTransport(answer(502), ConnectionError("reset"), answer(200, FAKE_JPEG))
    sleeps: list[float] = []

    content = make_client(transport, sleeps).download("2026/7", "111")

    assert content == FAKE_JPEG
    assert transport.urls == [PHOTO_URL] * 3
    # polite delay, backoff 20, polite delay, backoff 40, polite delay
    assert sleeps[1] == 20.0
    assert sleeps[3] == 40.0
    assert len(sleeps) == 5


def test_gives_up_as_unreachable_after_the_last_attempt_without_a_final_backoff() -> None:
    transport = FakeTransport(answer(503), answer(429), answer(500))
    sleeps: list[float] = []

    with pytest.raises(PhotoDownloadError) as caught:
        make_client(transport, sleeps).download("2026/7", "111")

    assert caught.value.reason == "unreachable"
    assert len(transport.urls) == 3
    assert [s for s in sleeps if s >= 20.0] == [20.0, 40.0]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (answer(404, b'{"message": "not found"}'), "not_found"),
        (answer(403), "unexpected_status"),
        (answer(200, b'{"not": "a photo"}'), "not_jpeg"),
        (answer(200, b""), "not_jpeg"),
        (answer(200, JPEG_SIGNATURE + b"x" * MAX_PHOTO_BYTES), "too_large"),
    ],
)
def test_unusable_answers_are_not_retried(response: TransportResponse, reason: str) -> None:
    transport = FakeTransport(response)

    error = download_error(transport)

    assert error.reason == reason
    assert len(transport.urls) == 1


def test_a_photo_of_exactly_the_maximum_size_is_accepted() -> None:
    photo = JPEG_SIGNATURE + b"x" * (MAX_PHOTO_BYTES - len(JPEG_SIGNATURE))
    transport = FakeTransport(answer(200, photo))

    assert make_client(transport, []).download("2026/7", "111") == photo


def test_the_reason_is_the_error_message() -> None:
    error = PhotoDownloadError("not_found")

    assert str(error) == "not_found"
    assert error.reason == "not_found"
