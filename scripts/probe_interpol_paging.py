"""Check whether paging alone reaches every notice of the Interpol API.

Interpol's website shows at most 160 notices per search, but the API answered page 2 of
a 160-sized page with 160 more notices. This script pages through one query until an
empty page comes back and counts how many *distinct* entity ids it saw, to tell real
new notices from repeated ones. Only counts are printed; the ids are kept in memory and
never written anywhere.

Run from anywhere:
    uv run python scripts/probe_interpol_paging.py
"""

import random
import time
from typing import Any

import curl_cffi

BASE_URL = "https://ws-public.interpol.int/notices/v1/red"
IMPERSONATE = "chrome120"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.interpol.int/",
    "Origin": "https://www.interpol.int",
}
PAGE_SIZE = 160
MAX_PAGES = 60  # safety stop: 60 x 160 = 9600, well above the 6382 notices seen so far

# The queries to page through: RU first (3007 notices), then every notice.
QUERIES: list[tuple[str, dict[str, str]]] = [
    ("nationality=RU", {"nationality": "RU"}),
    ("all notices", {}),
]


def fetch_page(params: dict[str, str], page: int) -> dict[str, Any] | None:
    """Return one parsed list page, or ``None`` if the request failed."""
    try:
        response = curl_cffi.get(
            BASE_URL,
            params={**params, "resultPerPage": PAGE_SIZE, "page": page},
            headers=HEADERS,
            impersonate=IMPERSONATE,
            timeout=60,
        )
        if response.status_code != 200:
            print(f"  page {page}: HTTP {response.status_code}", flush=True)
            return None
        body: dict[str, Any] = response.json()  # type: ignore[no-untyped-call]
        return body
    except Exception as error:
        print(f"  page {page}: error {type(error).__name__}", flush=True)
        return None
    finally:
        time.sleep(random.uniform(5.0, 8.0))


def probe(label: str, params: dict[str, str]) -> None:
    """Page through one query and print how many distinct notices it returned."""
    print(label, flush=True)
    seen: set[str] = set()
    total: Any = None
    for page in range(1, MAX_PAGES + 1):
        body = fetch_page(params, page)
        if body is None:
            break
        total = body.get("total")
        notices = body.get("_embedded", {}).get("notices", [])
        ids = {n["entity_id"] for n in notices if isinstance(n.get("entity_id"), str)}
        new = len(ids - seen)
        seen |= ids
        print(f"  page {page:>2}: returned={len(notices):>3} new={new:>3} distinct={len(seen)}")
        if not notices:
            break
    print(f"  total reported={total} distinct seen={len(seen)}", flush=True)


def main() -> None:
    """Run every paging probe."""
    for label, params in QUERIES:
        probe(label, params)


if __name__ == "__main__":
    main()
