"""Collects every notice id on the Interpol list by splitting queries (ADR 0006, 0007, 0009).

A query returns at most 160 notices, so a group above that size is split by sex, then
by the country that issued the arrest warrant, then by halving the age range, until
every group fits in one request. A single age that is still too large is *covered* by
surname substrings: every letter first, and a value still above the limit is extended
by one letter on each side (``V`` → ``VA`` ... ``VZ``, ``AV`` ... ``ZV``). The scanner
only reads the list; it does not fetch details or produce events.

Interpol's ``name`` filter matches surnames *containing* the value (measured for one to
three letters, ADR 0009), so a notice appears under several values and their totals do
not add up. A covered group is complete only when the distinct ids found reach the
group's total: that proves every notice of the group was seen.

Every id is tagged with the *region* it was seen in: its sex and the issuing country of
the query that returned it, for example ``M|RU``. Groups that could not be read in full
are reported by region too, so that removals can be held back only there (ADR 0010).
"""

import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

import pycountry

from interpol_collector.interpol_client import PAGE_SIZE, InterpolError, ListPage
from interpol_observability import get_logger

SEXES = ("M", "F", "U")
MIN_AGE = 0
MAX_AGE = 120
LETTERS = tuple(string.ascii_uppercase)

logger = get_logger(__name__)

Filters = Mapping[str, str | int]
SplitKind = Literal["sex", "issuer", "age", "name"]
# Longest surname substring tried before a group is given up as incomplete (ADR 0009).
MAX_SUBSTRING_LENGTH = 4
# Region of a query without a sex or issuing country filter (ADR 0010).
ANY = "*"
WHOLE_LIST = f"{ANY}|{ANY}"


class NoticeLister(Protocol):
    """Anything that answers a filtered list query; the Interpol client fits."""

    def list_notices(self, filters: Filters) -> ListPage: ...


@dataclass(frozen=True)
class ListScanResult:
    """Outcome of one list scan."""

    entity_ids: frozenset[str]
    complete: bool
    reported_total: int
    requests: int
    age_split_requests: int
    name_split_requests: int = 0
    # Regions each id was seen in, and regions that could not be read in full (ADR 0010).
    regions: Mapping[str, frozenset[str]] = field(default_factory=dict)
    incomplete_regions: frozenset[str] = frozenset()


@dataclass
class _Progress:
    """Mutable bookkeeping of a scan in progress."""

    entity_ids: set[str] = field(default_factory=set)
    regions: dict[str, set[str]] = field(default_factory=dict)
    incomplete_regions: set[str] = field(default_factory=set)
    stopped: bool = False
    requests: int = 0
    age_split_requests: int = 0
    name_split_requests: int = 0


@dataclass(frozen=True)
class _Split:
    """How a group too large for one request is divided.

    ``check`` says how the result is verified: ``sum`` the children's totals must add up
    to the group's (a partition; a difference is only logged), ``none`` no check (a notice
    can have warrants from several countries).
    """

    kind: SplitKind
    children: list[dict[str, str | int]]
    check: Literal["sum", "none"]


def region_of(filters: Filters) -> str:
    """Return the region of a query: its sex and issuing country, ``*`` when not set."""
    return f"{filters.get('sexId', ANY)}|{filters.get('arrestWarrantCountryId', ANY)}"


def regions_overlap(first: str, second: str) -> bool:
    """Return whether a notice in region ``first`` could also be in region ``second``."""
    return all(
        ANY in (a, b) or a == b for a, b in zip(first.split("|"), second.split("|"), strict=True)
    )


def _most_specific(regions: set[str]) -> frozenset[str]:
    """Drop regions that only repeat a more specific one, e.g. ``M|*`` next to ``M|RU``."""
    return frozenset(
        region
        for region in regions
        if not any(
            other != region
            and regions_overlap(other, region)
            and other.count(ANY) < region.count(ANY)
            for other in regions
        )
    )


def iso_country_codes() -> tuple[str, ...]:
    """Return the ISO 3166-1 alpha-2 codes used to split by issuing country."""
    return tuple(sorted(country.alpha_2 for country in pycountry.countries))


class ListScanner:
    """Runs one complete list scan against a NoticeLister."""

    def __init__(
        self,
        lister: NoticeLister,
        countries: Sequence[str],
        *,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self._lister = lister
        self._countries = tuple(countries)
        self._should_stop = should_stop

    def scan(self) -> ListScanResult:
        """Collect every visible notice id and decide whether the scan is complete."""
        progress = _Progress()
        root = self._fetch({}, progress)
        if root is None or progress.stopped:
            progress.incomplete_regions.add(WHOLE_LIST)
        else:
            self._collect({}, root, progress)
            if progress.stopped:
                progress.incomplete_regions.add(WHOLE_LIST)
        result = ListScanResult(
            entity_ids=frozenset(progress.entity_ids),
            complete=not progress.incomplete_regions,
            reported_total=root.total if root is not None else 0,
            requests=progress.requests,
            age_split_requests=progress.age_split_requests,
            name_split_requests=progress.name_split_requests,
            regions={eid: _most_specific(regions) for eid, regions in progress.regions.items()},
            incomplete_regions=frozenset(progress.incomplete_regions),
        )
        logger.info(
            "list_scan_finished",
            complete=result.complete,
            seen=len(result.entity_ids),
            reported_total=result.reported_total,
            requests=result.requests,
            age_split_requests=result.age_split_requests,
            name_split_requests=result.name_split_requests,
            incomplete_regions=sorted(result.incomplete_regions),
        )
        return result

    def _collect(
        self, filters: Filters, page: ListPage, progress: _Progress
    ) -> tuple[set[str], bool]:
        """Read a group and, if needed, its sub-groups.

        Return the ids found in this group and whether every notice of it was seen.
        """
        found = set(page.entity_ids)
        if page.total <= PAGE_SIZE:
            return found, True

        split = self._split(filters)
        if split is None:
            return self._collect_cover(filters, page, progress)

        children_ids: set[str] = set()
        children_total = 0
        children_complete = True
        for child in split.children:
            child_page = self._fetch(child, progress, split.kind)
            if child_page is None:
                children_complete = False
                if not progress.stopped:
                    progress.incomplete_regions.add(region_of(child))
                continue
            children_total += child_page.total
            child_ids, child_complete = self._collect(child, child_page, progress)
            children_ids |= child_ids
            children_complete = children_complete and child_complete

        if split.check == "sum" and children_complete and children_total != page.total:
            logger.warning(
                "list_split_totals_differ",
                filters=dict(filters),
                total=page.total,
                children_total=children_total,
            )
        return found | children_ids, children_complete

    def _collect_cover(
        self, filters: Filters, page: ListPage, progress: _Progress
    ) -> tuple[set[str], bool]:
        """Read a single-age group through surname substrings until everyone was seen.

        Every letter is queried first. A value still above the limit is extended by one
        letter on each side; every surname of at least two letters that contains ``V``
        also contains one of ``VA`` ... ``VZ`` or ``AV`` ... ``ZV``, so the extensions
        together cover the value. The ids of values above the limit count too, as they
        are real members of the group. Querying stops as soon as the distinct ids reach
        the group's total.
        """
        seen = set(page.entity_ids)
        pending = list(LETTERS)
        queried: set[str] = set()
        extended = 0
        while pending and len(seen) < page.total and not progress.stopped:
            value = pending.pop(0)
            if value in queried:
                continue
            queried.add(value)
            child_page = self._fetch({**filters, "name": value}, progress, "name")
            if child_page is None:
                continue
            seen.update(child_page.entity_ids)
            if child_page.total > PAGE_SIZE and len(value) < MAX_SUBSTRING_LENGTH:
                extended += 1
                pending.extend(f"{value}{letter}" for letter in LETTERS)
                pending.extend(f"{letter}{value}" for letter in LETTERS)
        # Seeing as many distinct ids as the group holds proves it was read in full.
        complete = len(seen) >= page.total and not progress.stopped
        if not complete and not progress.stopped:
            progress.incomplete_regions.add(region_of(filters))
            logger.error(
                "list_cover_incomplete",
                filters=dict(filters),
                total=page.total,
                seen=len(seen),
                extended_values=extended,
            )
        return seen, complete

    def _split(self, filters: Filters) -> _Split | None:
        """Return how to partition a group, or None for a single age (covered instead)."""
        if "sexId" not in filters:
            return _Split("sex", [{**filters, "sexId": sex} for sex in SEXES], "sum")
        if "arrestWarrantCountryId" not in filters:
            children = [{**filters, "arrestWarrantCountryId": code} for code in self._countries]
            return _Split("issuer", children, "none")
        low = int(filters.get("ageMin", MIN_AGE))
        high = int(filters.get("ageMax", MAX_AGE))
        if low < high:
            middle = (low + high) // 2
            halves: list[dict[str, str | int]] = [
                {**filters, "ageMin": low, "ageMax": middle},
                {**filters, "ageMin": middle + 1, "ageMax": high},
            ]
            return _Split("age", halves, "sum")
        # A single age: no partitioning filter is left, the group is covered instead.
        return None

    def _fetch(
        self, filters: Filters, progress: _Progress, kind: SplitKind | None = None
    ) -> ListPage | None:
        if self._should_stop():
            # Shutting down: send no more requests and let the scan end as incomplete.
            progress.stopped = True
            return None
        progress.requests += 1
        if kind == "age":
            progress.age_split_requests += 1
        elif kind == "name":
            progress.name_split_requests += 1
        try:
            page = self._lister.list_notices(filters)
        except InterpolError:
            logger.warning("list_query_failed", filters=dict(filters), exc_info=True)
            return None
        region = region_of(filters)
        progress.entity_ids.update(page.entity_ids)
        for entity_id in page.entity_ids:
            progress.regions.setdefault(entity_id, set()).add(region)
        return page
