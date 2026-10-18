"""ORM models of every table.

Every model must be imported here: Alembic only sees the tables registered on
``Base.metadata``, and a model that is never imported is never registered.
"""

from interpol_db.models.arrest_warrant import ArrestWarrant
from interpol_db.models.country import Country
from interpol_db.models.notice import Notice, notice_nationalities
from interpol_db.models.notice_photo import NoticePhoto

__all__ = ["ArrestWarrant", "Country", "Notice", "NoticePhoto", "notice_nationalities"]
