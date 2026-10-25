"""Decides which notices left the Interpol list, and keeps that state between scans.

A notice is removed only after it is missing from two consecutive complete list scans
(ADR 0006). Incomplete scans change nothing, and a missing or unreadable state file
never causes a removal: when in doubt, the tracker keeps notices rather than drop them.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from interpol_collector.list_scanner import ListScanResult
from interpol_collector.state_io import write_text_atomically
from interpol_observability import get_logger

DEFAULT_MAX_REMOVED_FRACTION = 0.05

logger = get_logger(__name__)


@dataclass(frozen=True)
class RemovalState:
    """What the tracker remembers between scans."""

    present: frozenset[str] = frozenset()
    missing_once: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RemovalDecision:
    """Result of comparing one scan with the previous state."""

    state: RemovalState
    removed: frozenset[str]
    blocked: frozenset[str] = frozenset()


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
    if not scan.complete:
        return RemovalDecision(state=state or RemovalState(), removed=frozenset())
    if state is None:
        return RemovalDecision(state=RemovalState(present=scan.entity_ids), removed=frozenset())

    absent = state.present - scan.entity_ids
    removed = absent & state.missing_once
    if len(removed) > max_removed_fraction * len(state.present):
        return RemovalDecision(state=state, removed=frozenset(), blocked=removed)

    missing_once = absent - removed
    new_state = RemovalState(present=scan.entity_ids | missing_once, missing_once=missing_once)
    return RemovalDecision(state=new_state, removed=removed)


class _StateDocument(BaseModel):
    """On-disk format of the state file."""

    version: Literal[1]
    present: list[str]
    missing_once: list[str]


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
        )

    def save(self, state: RemovalState) -> None:
        """Write the state atomically; a crash never leaves a half-written file."""
        document = _StateDocument(
            version=1,
            present=sorted(state.present),
            missing_once=sorted(state.missing_once),
        )
        write_text_atomically(self._path, document.model_dump_json())
