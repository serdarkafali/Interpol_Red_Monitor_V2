"""Decides which notices left the Interpol list, and keeps that state between scans.

A notice is removed only after it is missing from two consecutive list scans that read
its region in full (ADR 0006, ADR 0010). A region is the notice's sex and issuing
country (``M|RU``). When a scan cannot read some regions, notices last seen there are
held back, while removals elsewhere go on; a scan that could not read the list at all
changes nothing. A missing or unreadable state file never causes a removal: when in
doubt, the tracker keeps notices rather than drop them.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from interpol_collector.list_scanner import WHOLE_LIST, ListScanResult, regions_overlap
from interpol_collector.state_io import write_text_atomically
from interpol_observability import get_logger

DEFAULT_MAX_REMOVED_FRACTION = 0.05

logger = get_logger(__name__)


@dataclass(frozen=True)
class RemovalState:
    """What the tracker remembers between scans."""

    present: frozenset[str] = frozenset()
    missing_once: frozenset[str] = frozenset()
    # Regions each present notice was last seen in; an unknown id counts as anywhere.
    regions: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RemovalDecision:
    """Result of comparing one scan with the previous state."""

    state: RemovalState
    removed: frozenset[str]
    blocked: frozenset[str] = frozenset()
    # Absent notices kept because their region was not read in full (ADR 0010).
    held_back: frozenset[str] = frozenset()


def decide_removals(
    state: RemovalState | None,
    scan: ListScanResult,
    *,
    max_removed_fraction: float = DEFAULT_MAX_REMOVED_FRACTION,
) -> RemovalDecision:
    """Return the new state and the notices to remove after ``scan``.

    The function has no side effects; saving the state and producing events is the
    caller's job. If more than ``max_removed_fraction`` of the known notices would be
    removed at once, nothing is removed and the ids are returned as ``blocked``.
    """
    incomplete = scan.incomplete_regions or (
        frozenset() if scan.complete else frozenset({WHOLE_LIST})
    )
    if WHOLE_LIST in incomplete:
        return RemovalDecision(state=state or RemovalState(), removed=frozenset())
    if state is None:
        return RemovalDecision(
            state=RemovalState(present=scan.entity_ids, regions=dict(scan.regions)),
            removed=frozenset(),
        )

    absent = state.present - scan.entity_ids
    held_back = frozenset(
        entity_id
        for entity_id in absent
        if _may_be_in(state.regions.get(entity_id, frozenset({WHOLE_LIST})), incomplete)
    )
    candidates = absent - held_back
    removed = candidates & state.missing_once
    if len(removed) > max_removed_fraction * len(state.present):
        return RemovalDecision(
            state=state, removed=frozenset(), blocked=removed, held_back=held_back
        )

    missing_once = (candidates - removed) | (held_back & state.missing_once)
    present = (state.present - removed) | scan.entity_ids
    regions = {
        entity_id: scan.regions.get(entity_id) or state.regions.get(entity_id, frozenset())
        for entity_id in present
    }
    new_state = RemovalState(
        present=present,
        missing_once=missing_once,
        regions={entity_id: value for entity_id, value in regions.items() if value},
    )
    return RemovalDecision(state=new_state, removed=removed, held_back=held_back)


def _may_be_in(regions: frozenset[str], incomplete: frozenset[str]) -> bool:
    return any(regions_overlap(region, other) for region in regions for other in incomplete)


class _StateDocument(BaseModel):
    """On-disk format of the state file.

    Version 1 had no regions; such a file loads with every region unknown, which holds
    back every absent notice while any region is incomplete (the safe side).
    """

    version: Literal[1, 2]
    present: list[str]
    missing_once: list[str]
    regions: dict[str, list[str]] = {}


class StateFile:
    """Loads and saves a RemovalState as JSON; writes are atomic."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> RemovalState | None:
        """Return the saved state, or None if there is none or it cannot be trusted."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.info("removal_state_not_found", path=str(self._path))
            return None
        try:
            document = _StateDocument.model_validate_json(text)
        except ValidationError:
            logger.error("removal_state_unreadable", path=str(self._path), exc_info=True)
            return None
        return RemovalState(
            present=frozenset(document.present),
            missing_once=frozenset(document.missing_once),
            regions={
                entity_id: frozenset(regions) for entity_id, regions in document.regions.items()
            },
        )

    def save(self, state: RemovalState) -> None:
        """Write the state atomically; a crash never leaves a half-written file."""
        document = _StateDocument(
            version=2,
            present=sorted(state.present),
            missing_once=sorted(state.missing_once),
            regions={
                entity_id: sorted(regions) for entity_id, regions in sorted(state.regions.items())
            },
        )
        write_text_atomically(self._path, document.model_dump_json())
