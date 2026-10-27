"""Measure whether Interpol's ``name`` filter also matches multi-letter values as "contains".

The dry runs for ADR 0009 left one notice of ``sexId=M, arrestWarrantCountryId=RU,
age 37`` that no single-letter or nationality query reaches: every query it belongs to
returns more than 160 notices, and the capped first 160 never include it. Extending a
too-large letter to two letters (``VA`` ... ``VZ`` and ``AV`` ... ``ZV``) would cover every
surname containing that letter, provided a two-letter value also matches as a substring.

Part 1 reads a single-age group that fits in one query and compares what ``name=<two or
three letters>`` returns with what each matching rule would return.
Part 2 queries the problem group with every letter and with every two-letter extension of
``V`` and reports whether the distinct ids now reach the group's total.

Names stay in memory only: the script prints letters, counts and rule names, never a
name. About 100 requests with the collector's 5-8 s delay (11-12 minutes). The
results of 2026-10-27 are recorded in ADR 0009.

Run from the repository root:
    uv run python scripts/probe_interpol_name_substrings.py
"""

import random
import re
import string
import time
from collections import Counter
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
GROUP_BASE: dict[str, str | int] = {"sexId": "M", "arrestWarrantCountryId": "RU"}
PROBLEM_GROUP: dict[str, str | int] = {**GROUP_BASE, "ageMin": 37, "ageMax": 37}
LETTERS = string.ascii_uppercase
WORD_START = re.compile(r"(?:^|[^A-Z])")

Notice = dict[str, Any]


def fetch(params: dict[str, str | int]) -> tuple[int, list[Notice]] | str:
    """Return the total and the notices of a query's first page, or an error text."""
    try:
        response = curl_cffi.get(
            BASE_URL,
            params={**params, "resultPerPage": PAGE_SIZE, "page": 1},
            headers=HEADERS,
            impersonate=IMPERSONATE,
            timeout=60,
        )
        if response.status_code != 200:
            return f"HTTP {response.status_code}"
        body: dict[str, Any] = response.json()  # type: ignore[no-untyped-call]
        notices: list[Notice] = body.get("_embedded", {}).get("notices", [])
        return int(body["total"]), notices
    except Exception as error:
        return f"error: {type(error).__name__}"
    finally:
        time.sleep(random.uniform(5.0, 8.0))


def surname(notice: Notice) -> str:
    value = notice.get("name")
    return value.upper() if isinstance(value, str) else ""


def ids(notices: list[Notice]) -> set[str]:
    return {notice["entity_id"] for notice in notices}


def find_small_group() -> tuple[dict[str, str | int], dict[str, Notice]] | None:
    """Find a single age of the RU-issued male notices that fits in one query."""
    for age in (38, 36, 39, 35, 40, 34, 41, 33):
        params: dict[str, str | int] = {**GROUP_BASE, "ageMin": age, "ageMax": age}
        result = fetch(params)
        if isinstance(result, str):
            print(f"age {age}: {result}", flush=True)
            continue
        total, notices = result
        print(f"age {age}: total {total}", flush=True)
        if 60 <= total <= PAGE_SIZE:
            return params, {notice["entity_id"]: notice for notice in notices}
    return None


def part_one() -> None:
    print("== Part 1: how do multi-letter values match? ==", flush=True)
    found = find_small_group()
    if found is None:
        print("no single age between 60 and 160 notices found", flush=True)
        return
    params, group = found
    bigrams = Counter(
        name[i : i + 2]
        for name in map(surname, group.values())
        for i in range(len(name) - 1)
        if name[i : i + 2].isalpha()
    )
    common = [value for value, _ in bigrams.most_common(8)]
    trigrams = Counter(
        name[i : i + 3]
        for name in map(surname, group.values())
        for i in range(len(name) - 2)
        if name[i : i + 3].isalpha()
    )
    values = [*common, *(value for value, _ in trigrams.most_common(4)), "QX", "ZZ"]
    print(f"group of {len(group)} read; testing {len(values)} values", flush=True)
    print(f"{'value':<7}{'total':>6}  rule", flush=True)
    for value in values:
        result = fetch({**params, "name": value})
        if isinstance(result, str):
            print(f"{value:<7}{result:>6}", flush=True)
            continue
        total, notices = result
        returned = ids(notices)
        rules = {
            "contains": {eid for eid, n in group.items() if value in surname(n)},
            "starts": {eid for eid, n in group.items() if surname(n).startswith(value)},
            "word starts": {
                eid
                for eid, n in group.items()
                if re.search(WORD_START.pattern + re.escape(value), surname(n))
            },
            "reversed contains": {eid for eid, n in group.items() if value[::-1] in surname(n)},
        }
        fitting = [rule for rule, expected in rules.items() if expected == returned]
        print(f"{value:<7}{total:>6}  {', '.join(fitting) or 'none of the rules'}", flush=True)


def part_two() -> None:
    print("\n== Part 2: does extending V reach the problem group's total? ==", flush=True)
    result = fetch(PROBLEM_GROUP)
    if isinstance(result, str):
        print(f"group query failed: {result}", flush=True)
        return
    total, notices = result
    seen = ids(notices)
    print(f"group total {total}", flush=True)
    for letter in LETTERS:
        letter_result = fetch({**PROBLEM_GROUP, "name": letter})
        if not isinstance(letter_result, str):
            seen |= ids(letter_result[1])
    print(f"after the 26 letters: {len(seen)} of {total}", flush=True)
    too_large: list[str] = []
    for other in LETTERS:
        for value in (f"V{other}", f"{other}V"):
            pair_result = fetch({**PROBLEM_GROUP, "name": value})
            if isinstance(pair_result, str):
                print(f"{value}: {pair_result}", flush=True)
                continue
            pair_total, pair_notices = pair_result
            seen |= ids(pair_notices)
            if pair_total > PAGE_SIZE:
                too_large.append(f"{value}={pair_total}")
    print(f"after the 52 extensions of V: {len(seen)} of {total}", flush=True)
    print(f"extensions still above {PAGE_SIZE}: {too_large or 'none'}", flush=True)


def main() -> None:
    part_one()
    part_two()


if __name__ == "__main__":
    main()
