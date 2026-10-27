"""Client of the public Interpol Red Notice API.

This is the only place in the collector that talks to Interpol. It waits between
requests, retries temporary failures and refuses filters the API would silently
ignore (ADR 0006).
"""

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, ValidationError

from interpol_observability import get_logger

PAGE_SIZE = 160
# Filters measured to work; Interpol silently ignores unknown ones (ADR 0006, 0007, 0009).
ALLOWED_FILTERS = frozenset(
    ["sexId", "arrestWarrantCountryId", "nationality", "ageMin", "ageMax", "name", "forename"]
)
RETRYABLE_STATUS_CODES = frozenset([429, 500, 502, 503, 504])
RETRY_BACKOFF_SECONDS = 20.0

logger = get_logger(__name__)


class InterpolError(Exception):
    """The Interpol API could not be reached or returned an unusable response."""


class NoticeNotFoundError(InterpolError):
    """The requested notice does not exist (HTTP 404)."""


@dataclass(frozen=True)
class TransportResponse:
    """Status code and decoded JSON body of one response; body is None if not JSON."""

    status_code: int
    body: Any


class Transport(Protocol):
    """Anything that can send a GET request; the real one and the test fake both fit."""

    def get(self, url: str, params: Mapping[str, str | int]) -> TransportResponse: ...


@dataclass(frozen=True)
class ListPage:
    """Result of one list query: Interpol's total and the ids it returned (at most 160)."""

    total: int
    entity_ids: tuple[str, ...]


def to_url_id(entity_id: str) -> str:
    """Convert `2026/7` to the `2026-7` form used in Interpol URLs."""
    return entity_id.replace("/", "-")


class _ListedNotice(BaseModel):
    entity_id: str


class _ListEmbedded(BaseModel):
    notices: list[_ListedNotice] = []


class _ListResponse(BaseModel):
    """The part of a list response the collector uses; other fields are ignored."""

    total: NonNegativeInt
    embedded: _ListEmbedded = Field(default_factory=_ListEmbedded, alias="_embedded")


class _Image(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    picture_id: str


class _ImagesEmbedded(BaseModel):
    images: list[_Image] = []


class _ImagesResponse(BaseModel):
    """The part of an image list response the collector uses."""

    embedded: _ImagesEmbedded = Field(default_factory=_ImagesEmbedded, alias="_embedded")


class InterpolClient:
    """Reads notices from the Interpol API, politely and with retries (ADR 0006)."""

    def __init__(
        self,
        transport: Transport,
        base_url: str,
        *,
        delay_range: tuple[float, float] = (5.0, 8.0),
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._delay_range = delay_range
        self._max_attempts = max_attempts
        self._sleep = sleep

    def list_notices(self, filters: Mapping[str, str | int]) -> ListPage:
        """Return the first page (at most 160 notices) of a filtered list query."""
        unknown = set(filters) - ALLOWED_FILTERS
        if unknown:
            raise ValueError(f"unknown filters: {sorted(unknown)}")
        params: dict[str, str | int] = {**filters, "resultPerPage": PAGE_SIZE, "page": 1}
        url = self._base_url
        body = self._get_json(url, params)
        try:
            response = _ListResponse.model_validate(body)
        except ValidationError as exc:
            raise InterpolError(f"unexpected list response from {url}") from exc
        ids = tuple(notice.entity_id for notice in response.embedded.notices)
        return ListPage(total=response.total, entity_ids=ids)

    def get_detail(self, entity_id: str) -> dict[str, Any]:
        """Return the raw detail response of one notice."""
        return self._get_json(f"{self._base_url}/{to_url_id(entity_id)}", {})

    def get_picture_ids(self, entity_id: str) -> list[str]:
        """Return the picture ids in one notice's image list."""
        url = f"{self._base_url}/{to_url_id(entity_id)}/images"
        body = self._get_json(url, {})
        try:
            response = _ImagesResponse.model_validate(body)
        except ValidationError as exc:
            raise InterpolError(f"unexpected image response from {url}") from exc
        return [image.picture_id for image in response.embedded.images]

    def _send(self, url: str, params: Mapping[str, str | int]) -> TransportResponse | None:
        """Send one request and wait afterwards; return None if it failed to complete."""
        try:
            return self._transport.get(url, params)
        except Exception:
            # Any transport failure (connection, timeout, TLS) is treated as temporary;
            # the caller decides whether to retry.
            logger.warning("interpol_request_failed", url=url, exc_info=True)
            return None
        finally:
            self._sleep(random.uniform(*self._delay_range))

    def _get_json(self, url: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        """Return the JSON object at ``url``, retrying temporary failures with backoff."""
        for attempt in range(1, self._max_attempts + 1):
            response = self._send(url, params)

            # No response at all (network failure) or a temporary server error: try again.
            if response is None or response.status_code in RETRYABLE_STATUS_CODES:
                if attempt < self._max_attempts:
                    backoff = RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1)
                    logger.warning(
                        "interpol_retry",
                        url=url,
                        attempt=attempt,
                        status_code=None if response is None else response.status_code,
                        backoff_seconds=backoff,
                    )
                    self._sleep(backoff)
                continue

            if response.status_code == 404:
                raise NoticeNotFoundError(f"notice not found: {url}")
            # The server answered, but not with a usable JSON object; retrying won't help.
            if response.status_code != 200 or not isinstance(response.body, dict):
                raise InterpolError(f"unexpected response from {url}: HTTP {response.status_code}")
            return response.body

        raise InterpolError(f"giving up on {url} after {self._max_attempts} attempts")
