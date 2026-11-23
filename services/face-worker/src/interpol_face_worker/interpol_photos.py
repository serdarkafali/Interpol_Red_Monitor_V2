"""Downloads notice photos from the public Interpol API.

This is the only place in the face worker that talks to Interpol. It waits between
requests, retries temporary failures and checks that what arrived is a JPEG of a
reasonable size (ADR 0011).
"""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from interpol_observability import get_logger

RETRYABLE_STATUS_CODES = frozenset([429, 500, 502, 503, 504])
RETRY_BACKOFF_SECONDS = 20.0
# Every photo measured on 2026-11-23 started with this signature (ADR 0011).
JPEG_SIGNATURE = b"\xff\xd8\xff"
MAX_PHOTO_BYTES = 5 * 1024 * 1024

logger = get_logger(__name__)


class PhotoDownloadError(Exception):
    """A photo could not be downloaded or is not a usable JPEG.

    ``reason`` is a fixed code (not_found, unreachable, unexpected_status, not_jpeg,
    too_large), so it can be logged and counted; it never contains the photo.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TransportResponse:
    """Status code and raw body of one response."""

    status_code: int
    content: bytes


class Transport(Protocol):
    """Anything that can send a GET request; the real one and the test fake both fit."""

    def get(self, url: str) -> TransportResponse: ...


def to_url_id(entity_id: str) -> str:
    """Convert `2026/7` to the `2026-7` form used in Interpol URLs."""
    return entity_id.replace("/", "-")


class PhotoClient:
    """Downloads photos from the Interpol API, politely and with retries."""

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

    def download(self, entity_id: str, picture_id: str) -> bytes:
        """Return the JPEG bytes of one photo; raise PhotoDownloadError otherwise."""
        url = f"{self._base_url}/{to_url_id(entity_id)}/images/{picture_id}"
        content = self._get_bytes(url)
        if len(content) > MAX_PHOTO_BYTES:
            raise PhotoDownloadError("too_large")
        if not content.startswith(JPEG_SIGNATURE):
            raise PhotoDownloadError("not_jpeg")
        return content

    def _send(self, url: str) -> TransportResponse | None:
        """Send one request and wait afterwards; return None if it failed to complete."""
        try:
            return self._transport.get(url)
        except Exception:
            # Any transport failure (connection, timeout, TLS) is treated as temporary;
            # the caller decides whether to retry.
            logger.warning("interpol_request_failed", url=url, exc_info=True)
            return None
        finally:
            self._sleep(random.uniform(*self._delay_range))

    def _get_bytes(self, url: str) -> bytes:
        """Return the body at ``url``, retrying temporary failures with backoff."""
        for attempt in range(1, self._max_attempts + 1):
            response = self._send(url)

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
                raise PhotoDownloadError("not_found")
            # The server answered with something other than the photo; retrying won't help.
            if response.status_code != 200:
                raise PhotoDownloadError("unexpected_status")
            return response.content

        raise PhotoDownloadError("unreachable")
