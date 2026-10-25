"""Collects every notice id on the Interpol list by splitting queries (ADR 0006, 0007).

A query returns at most 160 notices, so a group above that size is split by sex, then
by the country that issued the arrest warrant, then by halving the age range, and a
single age that is still too large by nationality, until every group fits in one
request. The scanner only reads the list; it does not fetch details or produce events.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import pycountry

from interpol_collector.interpol_client import PAGE_SIZE, InterpolError, ListPage
from interpol_observability import get_logger

SEXES = ("M", "F", "U")
MIN_AGE = 0
MAX_AGE = 120

logger = get_logger(__name__)

Filters = Mapping[str, str | int]


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


@dataclass
class _Progress:
    """Mutable bookkeeping of a scan in progress."""

    entity_ids: set[str] = field(default_factory=set)
    complete: bool = True
    requests: int = 0
    age_split_requests: int = 0


def iso_country_codes() -> tuple[str, ...]:
    """Return the ISO 3166-1 alpha-2 codes used to split by issuing country and nationality."""
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
        if root is not None:
            self._collect({}, root, progress)
        result = ListScanResult(
            entity_ids=frozenset(progress.entity_ids),
            complete=progress.complete,
            reported_total=root.total if root is not None else 0,
            requests=progress.requests,
            age_split_requests=progress.age_split_requests,
        )
        logger.info(
            "list_scan_finished",
            complete=result.complete,
            seen=len(result.entity_ids),
            reported_total=result.reported_total,
            requests=result.requests,
            age_split_requests=result.age_split_requests,
        )
        return result

    def _collect(self, filters: Filters, page: ListPage, progress: _Progress) -> None:
        progress.entity_ids.update(page.entity_ids)
        if page.total <= PAGE_SIZE:
            return

        split = self._split(filters)
        if split is None:
            logger.error("list_group_cannot_be_split", filters=dict(filters), total=page.total)
            progress.complete = False
            return

        children, totals_must_match = split
        children_total = 0
        all_fetched = True
        for child in children:
            child_page = self._fetch(child, progress)
            if child_page is None:
                all_fetched = False
                continue
            children_total += child_page.total
            self._collect(child, child_page, progress)

        if totals_must_match and all_fetched and children_total != page.total:
            logger.warning(
                "list_split_totals_differ",
                filters=dict(filters),
                total=page.total,
                children_total=children_total,
            )

    def _split(self, filters: Filters) -> tuple[list[dict[str, str | int]], bool] | None:
        """Return the sub-queries of a group and whether their totals must add up."""
        if "sexId" not in filters:
            return [{**filters, "sexId": sex} for sex in SEXES], True
        if "arrestWarrantCountryId" not in filters:
            return [{**filters, "arrestWarrantCountryId": code} for code in self._countries], False
        low = int(filters.get("ageMin", MIN_AGE))
        high = int(filters.get("ageMax", MAX_AGE))
        if low < high:
            middle = (low + high) // 2
            halves: list[dict[str, str | int]] = [
                {**filters, "ageMin": low, "ageMax": middle},
                {**filters, "ageMin": middle + 1, "ageMax": high},
            ]
            return halves, True
        if "nationality" not in filters:
            return [{**filters, "nationality": code} for code in self._countries], False
        return None

    def _fetch(self, filters: Filters, progress: _Progress) -> ListPage | None:
        if self._should_stop():
            # Shutting down: send no more requests and let the scan end as incomplete.
            progress.complete = False
            return None
        progress.requests += 1
        if "ageMin" in filters:
            progress.age_split_requests += 1
        try:
            return self._lister.list_notices(filters)
        except InterpolError:
            logger.warning("list_query_failed", filters=dict(filters), exc_info=True)
            progress.complete = False
            return None
