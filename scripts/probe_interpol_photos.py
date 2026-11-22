"""Measure what Interpol returns when a notice photo is downloaded (ADR 0011).

The face worker will download full-size photos. Before its client is written we need to
know the content type and size of a photo response, and what a missing photo returns.

No photo is stored or printed: for each response only the status code, the content type,
the size in bytes and the first three bytes (the file signature, e.g. ffd8ff for JPEG)
are printed. About 12 requests with the collector's 5-8 s delay, so it runs for one to
two minutes.

Run from the repository root:
    uv run python scripts/probe_interpol_photos.py
"""

import random
import time
from typing import Any

import curl_cffi
from curl_cffi import Response

BASE_URL = "https://ws-public.interpol.int/notices/v1/red"
IMPERSONATE = "chrome120"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.interpol.int/",
    "Origin": "https://www.interpol.int",
}
SAMPLE_SIZE = 5
MISSING_PICTURE_ID = "0"


def get(url: str, params: dict[str, str | int] | None = None) -> Response:
    """Send one request and wait the collector's polite delay afterwards."""
    try:
        return curl_cffi.get(
            url, params=params, headers=HEADERS, impersonate=IMPERSONATE, timeout=60
        )
    finally:
        time.sleep(random.uniform(5.0, 8.0))


def to_url_id(entity_id: str) -> str:
    """Entity ids look like 2024/12345; the API's URLs use 2024-12345."""
    return entity_id.replace("/", "-")


def first_entity_ids(count: int) -> list[str]:
    """The entity ids on the first page of the list."""
    body: dict[str, Any] = get(BASE_URL, {"resultPerPage": count, "page": 1}).json()
    return [str(notice["entity_id"]) for notice in body["_embedded"]["notices"]]


def picture_ids(entity_id: str) -> list[str]:
    """The picture ids in one notice's image list."""
    body: dict[str, Any] = get(f"{BASE_URL}/{to_url_id(entity_id)}/images").json()
    return [str(image["picture_id"]) for image in body.get("_embedded", {}).get("images", [])]


def describe(entity_id: str, picture_id: str) -> None:
    """Download one photo and print only facts about the response, never its content."""
    response = get(f"{BASE_URL}/{to_url_id(entity_id)}/images/{picture_id}")
    content_type = response.headers.get("Content-Type", "-")
    print(
        f"{entity_id:<14} {picture_id:<12} {response.status_code:<6} "
        f"{content_type:<28} {len(response.content):>9} {response.content[:3].hex()}",
        flush=True,
    )


def main() -> None:
    print(
        f"{'entity_id':<14} {'picture_id':<12} {'status':<6} {'content type':<28} "
        f"{'bytes':>9} start",
        flush=True,
    )
    entity_ids = first_entity_ids(SAMPLE_SIZE)
    for entity_id in entity_ids:
        pictures = picture_ids(entity_id)
        if not pictures:
            print(f"{entity_id:<14} (no photos)", flush=True)
            continue
        describe(entity_id, pictures[0])
    print("\nA picture id that does not exist:", flush=True)
    describe(entity_ids[0], MISSING_PICTURE_ID)


if __name__ == "__main__":
    main()
