"""Database schema of the Interpol monitor: ORM models and Alembic migrations.

Only table definitions live here. Queries and business logic belong to the services
that use the database.
"""

from interpol_db.base import NAMING_CONVENTION, Base
from interpol_db.models import (
    ArrestWarrant,
    Country,
    Notice,
    NoticePhoto,
    notice_nationalities,
)

__all__ = [
    "NAMING_CONVENTION",
    "ArrestWarrant",
    "Base",
    "Country",
    "Notice",
    "NoticePhoto",
    "notice_nationalities",
]
