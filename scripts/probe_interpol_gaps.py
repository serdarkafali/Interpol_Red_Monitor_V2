"""Measure the two gaps found in the first real dry run of the collector (2026-10-25).

1. ``sexId=M, nationality=RU, age 37`` holds 162 notices: more than one query can return,
   and the age cannot be split further. Is there another filter that splits it?
2. About 100 notices were never seen. Do they carry nationality codes that are not in
   pycountry's current ISO 3166-1 list (Kosovo, historic codes, ...)?

Only totals are printed; no notice data is read or stored. About 280 requests with the
collector's 5-8 s delay, so it runs for roughly half an hour. The results of 2026-10-25
are recorded in ADR 0007.

Run from the repository root:
    uv run python scripts/probe_interpol_gaps.py
"""

import random
import time
from typing import Any

import curl_cffi
import pycountry

BASE_URL = "https://ws-public.interpol.int/notices/v1/red"
IMPERSONATE = "chrome120"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.interpol.int/",
    "Origin": "https://www.interpol.int",
}
RU_37: dict[str, str | int] = {"sexId": "M", "nationality": "RU", "ageMin": 37, "ageMax": 37}
EXTRA_NATIONALITIES = ["XK", "UN", "XX", "ZZ", "QQ"]


def fetch_total(params: dict[str, str | int]) -> int | str:
    """Return the ``total`` of a query, or a short error description."""
    try:
        response = curl_cffi.get(
            BASE_URL,
            params={**params, "resultPerPage": 1, "page": 1},
            headers=HEADERS,
            impersonate=IMPERSONATE,
            timeout=60,
        )
        if response.status_code != 200:
            return f"HTTP {response.status_code}"
        body: dict[str, Any] = response.json()  # type: ignore[no-untyped-call]
        return int(body["total"])
    except Exception as error:
        return f"error: {type(error).__name__}"
    finally:
        time.sleep(random.uniform(5.0, 8.0))


def show(label: str, params: dict[str, str | int]) -> int | str:
    """Print one probe and return its total."""
    total = fetch_total(params)
    print(f"{label:<52} {total}", flush=True)
    return total


def current_codes() -> list[str]:
    return sorted(country.alpha_2 for country in pycountry.countries)


def historic_only_codes() -> list[str]:
    """Codes of countries that no longer exist and were not reused by a current one."""
    current = set(current_codes())
    historic = {country.alpha_2 for country in pycountry.historic_countries}
    return sorted(historic - current)


def main() -> None:
    print("== Do the candidate filters work? (6378 = ignored) ==", flush=True)
    show("all notices", {})
    show("arrestWarrantCountryId=RU", {"arrestWarrantCountryId": "RU"})
    show("nationality=QQ (a code that does not exist)", {"nationality": "QQ"})

    print("\n== Nationality codes outside the current ISO list ==", flush=True)
    for code in EXTRA_NATIONALITIES + historic_only_codes():
        show(f"nationality={code}", {"nationality": code})

    print("\n== M, RU, age 37 by issuing country ==", flush=True)
    show("M, RU, age 37", RU_37)
    found: list[tuple[str, int]] = []
    for code in current_codes():
        total = fetch_total({**RU_37, "arrestWarrantCountryId": code})
        if isinstance(total, int) and total > 0:
            found.append((code, total))
            print(f"  issued by {code:<4} {total}", flush=True)
        elif not isinstance(total, int):
            print(f"  issued by {code:<4} {total}", flush=True)
    print(f"sum over issuing countries: {sum(total for _, total in found)}", flush=True)


if __name__ == "__main__":
    main()
