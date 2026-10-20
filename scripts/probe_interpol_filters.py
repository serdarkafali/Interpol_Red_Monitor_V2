"""Check which list filters of the Interpol API exist and how they split the results.

The collector has to work around the 160-result limit by splitting a query into smaller
ones (ADR 0005). Before designing that split, this script asks the API how many notices
each candidate filter returns and how pages behave. Only counts are printed; no notice
data is read or stored.

Run from anywhere:
    uv run python scripts/probe_interpol_filters.py
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

# Each probe is a label, the query parameters to send and the page size to ask for.
# A page size of 1 keeps the response small when only the total is needed. The results
# of 2026-10-20 are recorded in ADR 0006.
PROBES: list[tuple[str, dict[str, str | int], int]] = [
    ("all notices", {}, 1),
    ("all, sexId=M", {"sexId": "M"}, 1),
    ("all, sexId=F", {"sexId": "F"}, 1),
    ("all, sexId=U", {"sexId": "U"}, 1),
    ("all, age 0-120", {"ageMin": 0, "ageMax": 120}, 1),
    ("all, sexId=M, age 0-120", {"sexId": "M", "ageMin": 0, "ageMax": 120}, 1),
    ("all, sexId=M, age 121-250", {"sexId": "M", "ageMin": 121, "ageMax": 250}, 1),
    ("all, sexId=M, age 40-40", {"sexId": "M", "ageMin": 40, "ageMax": 40}, 1),
    ("nationality=RU", {"nationality": "RU"}, 1),
    ("RU, page size 160, page 2", {"nationality": "RU", "page": 2}, 160),
    ("RU, page size 200", {"nationality": "RU"}, 200),
    ("unknown filter only", {"thisFilterDoesNotExist": "x"}, 1),
]


def fetch_counts(params: dict[str, str | int], page_size: int) -> str:
    """Return the ``total`` and the number of notices returned, or an error description."""
    try:
        response = curl_cffi.get(
            BASE_URL,
            params={"page": 1, **params, "resultPerPage": page_size},
            headers=HEADERS,
            impersonate=IMPERSONATE,
            timeout=60,
        )
        if response.status_code != 200:
            return f"HTTP {response.status_code}"
        body: dict[str, Any] = response.json()  # type: ignore[no-untyped-call]
        returned = len(body.get("_embedded", {}).get("notices", []))
        return f"total={body.get('total')} returned={returned}"
    except Exception as error:
        return f"error: {type(error).__name__}"
    finally:
        time.sleep(random.uniform(5.0, 8.0))


def main() -> None:
    """Print the counts of every probe."""
    for label, params, page_size in PROBES:
        print(f"{label:<28} {fetch_counts(params, page_size)}", flush=True)


if __name__ == "__main__":
    main()
