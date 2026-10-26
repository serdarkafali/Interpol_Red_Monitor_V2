"""Turns one Kafka message into one database transaction and classifies failures (ADR 0008).

The handler raises one of two exceptions so that the consumer loop knows what to do:

- ``DeadLetterError``: the message can never be stored; retrying gives the same result.
  The loop writes it to the dead letter topic and moves on.
- ``TransientError``: the database could not be reached. The loop waits and retries the
  same message, without touching the dead letter topic.

Any other exception is a programming error and stops the service.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import assert_never

from pydantic import ValidationError
from sqlalchemy import Connection
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError, OperationalError

from interpol_contracts import NoticeEvent, NoticeObserved, NoticeRemoved, NoticeSeen, parse_event
from interpol_ingestor.normalize import RejectedNoticeError, normalize_notice
from interpol_ingestor.repository import (
    ObservedOutcome,
    apply_observed,
    apply_removed,
    apply_seen,
)
from interpol_observability import get_logger

logger = get_logger(__name__)

Transaction = Callable[[], AbstractContextManager[Connection]]


class DeadLetterError(Exception):
    """The message cannot be stored and goes to the dead letter topic.

    ``error_type`` and ``detail`` are fixed codes, field paths or SQLSTATE codes, never
    values from the message: they end up in logs and Kafka headers.
    """

    def __init__(self, error_type: str, detail: str = "") -> None:
        super().__init__(f"{error_type}: {detail}" if detail else error_type)
        self.error_type = error_type
        self.detail = detail


class TransientError(Exception):
    """The database could not be reached; the same message should be retried."""


class EventHandler:
    """Applies notice events to the database, one transaction per message."""

    def __init__(self, transaction: Transaction, *, ignore_content_hash: bool = False) -> None:
        self._transaction = transaction
        self._ignore_content_hash = ignore_content_hash

    def handle(self, value: bytes) -> None:
        """Parse one message value and write it; raise on failure (see module docstring)."""
        event = _parse(value)
        try:
            with self._transaction() as connection:
                self._apply(connection, event)
        except RejectedNoticeError as exc:
            raise DeadLetterError("notice_rejected", exc.reason) from exc
        except (IntegrityError, DataError) as exc:
            raise DeadLetterError("database_rejected", _sqlstate(exc)) from exc
        except DBAPIError as exc:
            if isinstance(exc, OperationalError) or exc.connection_invalidated:
                raise TransientError(type(exc.orig).__name__) from exc
            raise

    def _apply(self, connection: Connection, event: NoticeEvent) -> None:
        match event:
            case NoticeObserved():
                self._apply_observed(connection, event)
            case NoticeSeen():
                if apply_seen(connection, event.entity_id, event.occurred_at):
                    logger.info("notice_returned", entity_id=event.entity_id)
            case NoticeRemoved():
                if apply_removed(connection, event.entity_id, event.occurred_at):
                    logger.info("notice_removed", entity_id=event.entity_id)
            case _:
                # mypy fails here if a new event type is added but not handled.
                assert_never(event)

    def _apply_observed(self, connection: Connection, event: NoticeObserved) -> None:
        normalized = normalize_notice(event.entity_id, event.payload)
        if normalized.issues:
            logger.warning(
                "notice_values_dropped", entity_id=event.entity_id, issues=normalized.issues
            )
        result = apply_observed(
            connection,
            normalized.record,
            event.occurred_at,
            ignore_content_hash=self._ignore_content_hash,
        )
        if result.added_country_codes:
            logger.warning(
                "unknown_country_codes_added",
                entity_id=event.entity_id,
                codes=sorted(result.added_country_codes),
            )
        if result.outcome is not ObservedOutcome.UNCHANGED:
            logger.info("notice_written", entity_id=event.entity_id, outcome=result.outcome.value)


def _parse(value: bytes) -> NoticeEvent:
    try:
        return parse_event(value)
    except ValidationError as exc:
        # Only the paths of the invalid fields (or the error type when the whole value is
        # broken, e.g. not JSON): the error text contains the values.
        paths = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or error["type"]
                for error in exc.errors()
            }
        )
        raise DeadLetterError("invalid_message", ",".join(paths)) from exc


def _sqlstate(exc: DBAPIError) -> str:
    """The SQLSTATE code of a database error, or the driver's error class name."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else type(exc.orig).__name__
