"""Turns Interpol's raw notice detail into the values the database stores (ADR 0008).

Everything here is a pure function: no database, no logging. A value that does not fit
the schema becomes ``None`` and is reported as an issue code, so the caller can log it
next to the entity id without logging the value itself (it may be personal data). Only a
broken arrest warrant rejects the whole notice, because the warrant is what a red notice
rests on.
"""

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from interpol_contracts import NoticeDetail, NoticeSnapshot

SEX_IDS = frozenset({"M", "F", "U"})
COUNTRY_CODE = re.compile(r"[A-Z]{2}")
DATE_OF_BIRTH_FORMAT = "%Y/%m/%d"
# Same bounds as the CHECK constraints of the notices table (ADR 0004).
MAX_HEIGHT = Decimal(3)
MAX_WEIGHT = 500
_CENTIMETRE = Decimal("0.01")


class RejectedNoticeError(Exception):
    """The notice cannot be stored; the message goes to the dead letter topic.

    ``reason`` is a fixed code, never a value from the notice.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, kw_only=True)
class WarrantRecord:
    """One arrest warrant, ready for the arrest_warrants table."""

    issuing_country_code: str
    charge: str
    charge_translation: str | None


@dataclass(frozen=True, kw_only=True)
class NoticeRecord:
    """One notice with its children, in the shape of the database schema."""

    entity_id: str
    content_hash: str
    name: str
    forename: str | None
    sex_id: str | None
    date_of_birth: date | None
    place_of_birth: str | None
    country_of_birth_code: str | None
    height: Decimal | None
    weight: int | None
    distinguishing_marks: str | None
    language_codes: tuple[str, ...]
    eye_color_codes: tuple[str, ...]
    hair_color_codes: tuple[str, ...]
    thumbnail_picture_id: str | None
    nationality_codes: tuple[str, ...]
    arrest_warrants: tuple[WarrantRecord, ...]
    picture_ids: tuple[str, ...]

    @property
    def country_codes(self) -> frozenset[str]:
        """Every country code the notice refers to, for the countries table."""
        codes = {*self.nationality_codes, *(w.issuing_country_code for w in self.arrest_warrants)}
        if self.country_of_birth_code is not None:
            codes.add(self.country_of_birth_code)
        return frozenset(codes)


@dataclass(frozen=True)
class NormalizedNotice:
    """The record to store and the issue codes found on the way."""

    record: NoticeRecord
    issues: tuple[str, ...]


def normalize_notice(entity_id: str, snapshot: NoticeSnapshot) -> NormalizedNotice:
    """Convert a ``notice.observed`` payload into a database record.

    Raises ``RejectedNoticeError`` if an arrest warrant is unusable.
    """
    detail = snapshot.detail
    issues: list[str] = []
    record = NoticeRecord(
        entity_id=entity_id,
        content_hash=snapshot.content_hash,
        name=detail.name,
        forename=optional_text(detail.forename),
        sex_id=_sex_id(detail.sex_id, issues),
        date_of_birth=_date_of_birth(detail.date_of_birth, issues),
        place_of_birth=optional_text(detail.place_of_birth),
        country_of_birth_code=_country_of_birth(detail.country_of_birth_id, issues),
        height=_height(detail.height, issues),
        weight=_weight(detail.weight, issues),
        distinguishing_marks=optional_text(detail.distinguishing_marks),
        language_codes=tuple(detail.languages_spoken_ids or ()),
        eye_color_codes=tuple(detail.eyes_colors_id or ()),
        hair_color_codes=tuple(detail.hairs_id or ()),
        thumbnail_picture_id=optional_text(snapshot.thumbnail_picture_id),
        nationality_codes=_nationalities(detail.nationalities, issues),
        arrest_warrants=_arrest_warrants(detail),
        picture_ids=_unique(snapshot.picture_ids),
    )
    return NormalizedNotice(record, tuple(issues))


def optional_text(value: str | None) -> str | None:
    """Return ``None`` for missing, empty or blank text, otherwise the text unchanged."""
    if value is None or not value.strip():
        return None
    return value


def _sex_id(value: str | None, issues: list[str]) -> str | None:
    if value is None:
        return None
    if value not in SEX_IDS:
        issues.append("sex_id_unknown")
        return None
    return value


def _date_of_birth(value: str | None, issues: list[str]) -> date | None:
    text = optional_text(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, DATE_OF_BIRTH_FORMAT).date()
    except ValueError:
        issues.append("date_of_birth_unparsable")
        return None


def _height(value: float | None, issues: list[str]) -> Decimal | None:
    # Interpol uses 0 for "unknown"; that is not an issue.
    if value is None or value == 0:
        return None
    if not math.isfinite(value):
        issues.append("height_out_of_range")
        return None
    height = Decimal(str(value)).quantize(_CENTIMETRE, rounding=ROUND_HALF_UP)
    if not 0 < height < MAX_HEIGHT:
        issues.append("height_out_of_range")
        return None
    return height


def _weight(value: float | None, issues: list[str]) -> int | None:
    if value is None or value == 0:
        return None
    if not math.isfinite(value):
        issues.append("weight_out_of_range")
        return None
    weight = int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if not 0 < weight < MAX_WEIGHT:
        issues.append("weight_out_of_range")
        return None
    return weight


def _country_of_birth(value: str | None, issues: list[str]) -> str | None:
    code = optional_text(value)
    if code is None:
        return None
    if not COUNTRY_CODE.fullmatch(code):
        issues.append("country_of_birth_code_invalid")
        return None
    return code


def _nationalities(values: list[str] | None, issues: list[str]) -> tuple[str, ...]:
    valid = [code for code in values or () if COUNTRY_CODE.fullmatch(code)]
    if len(valid) != len(values or ()):
        issues.append("nationality_code_invalid")
    return _unique(valid)


def _arrest_warrants(detail: NoticeDetail) -> tuple[WarrantRecord, ...]:
    warrants = []
    for warrant in detail.arrest_warrants or ():
        if not COUNTRY_CODE.fullmatch(warrant.issuing_country_id):
            raise RejectedNoticeError("arrest_warrant_country_invalid")
        if optional_text(warrant.charge) is None:
            raise RejectedNoticeError("arrest_warrant_charge_empty")
        warrants.append(
            WarrantRecord(
                issuing_country_code=warrant.issuing_country_id,
                charge=warrant.charge,
                charge_translation=optional_text(warrant.charge_translation),
            )
        )
    return tuple(warrants)


def _unique(values: list[str]) -> tuple[str, ...]:
    """Drop repeated values and keep the first occurrence's order."""
    return tuple(dict.fromkeys(values))
