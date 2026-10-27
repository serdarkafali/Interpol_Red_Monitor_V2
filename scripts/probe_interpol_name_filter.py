"""Measure how Interpol's ``name`` and ``forename`` list filters match (after ADR 0007).

The list group ``sexId=M, arrestWarrantCountryId=RU, age 37, nationality=RU`` holds
exactly 160 notices: the most one query can return, and every known filter is already
used. If ``name=<letter>`` reliably splits a group, the list scanner can use the first
letter of the name as a further split step. That needs two answers:

1. How does the filter match: names *starting* with the letter, names with a *word*
   starting with it, names *containing* it, or something else (exact, fuzzy)?
2. Do the 26 letters together cover every notice of the group?

The group is read once in full; then each letter is queried for ``name`` and
``forename`` and the returned ids are compared with what each matching rule would
return. Names stay in memory only: the script prints letters, counts and the matching
rule, never a name. About 53 requests with the collector's 5-8 s delay (6-7 minutes).
The results of 2026-10-26 are recorded in ADR 0009.

Run from the repository root:
    uv run python scripts/probe_interpol_name_filter.py
"""

import random
import re
import string
import time
from collections.abc import Callable
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
GROUP: dict[str, str | int] = {
    "sexId": "M",
    "arrestWarrantCountryId": "RU",
    "ageMin": 37,
    "ageMax": 37,
    "nationality": "RU",
}
FIELDS = ("name", "forename")
WORD_START = re.compile(r"(?:^|[^A-Z])([A-Z])")

Notice = dict[str, Any]
Rule = Callable[[str, str], bool]
RULES: dict[str, Rule] = {
    "starts": lambda value, letter: value.startswith(letter),
    "word": lambda value, letter: letter in WORD_START.findall(value),
    "contains": lambda value, letter: letter in value,
}


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


def field_value(notice: Notice, field: str) -> str:
    value = notice.get(field)
    return value.upper() if isinstance(value, str) else ""


def probe_field(field: str, group: dict[str, Notice]) -> None:
    """Query every letter for one field and print which matching rule fits."""
    print(f"\n== {field}=<letter> ==", flush=True)
    print(f"{'letter':<7}{'total':>6}  rule", flush=True)
    covered: set[str] = set()
    fits = dict.fromkeys(RULES, 0)
    for letter in string.ascii_uppercase:
        result = fetch({**GROUP, field: letter})
        if isinstance(result, str):
            print(f"{letter:<7}{result:>6}", flush=True)
            continue
        total, notices = result
        returned = {notice["entity_id"] for notice in notices}
        covered |= returned
        matching = [
            name
            for name, rule in RULES.items()
            if returned
            == {eid for eid, notice in group.items() if rule(field_value(notice, field), letter)}
        ]
        for name in matching:
            fits[name] += 1
        if returned == set(group):
            label = "ignored (whole group returned)"
        else:
            label = ", ".join(matching) or "none of the rules"
        outside = len(returned - set(group))
        extra = f"  ({outside} ids outside the group!)" if outside else ""
        print(f"{letter:<7}{total:>6}  {label}{extra}", flush=True)

    empty = sum(1 for notice in group.values() if not field_value(notice, field))
    print(f"letters that fit each rule: {fits}", flush=True)
    print(f"covered by the 26 letters: {len(covered & set(group))} of {len(group)}", flush=True)
    print(f"notices with an empty {field}: {empty}", flush=True)


def main() -> None:
    print("== The group ==", flush=True)
    result = fetch(GROUP)
    if isinstance(result, str):
        raise SystemExit(f"group query failed: {result}")
    total, notices = result
    group = {notice["entity_id"]: notice for notice in notices}
    print(f"total {total}, read {len(group)}", flush=True)
    if total > PAGE_SIZE:
        print("the group no longer fits in one query: comparisons are only partial", flush=True)
    for field in FIELDS:
        probe_field(field, group)


if __name__ == "__main__":
    main()
