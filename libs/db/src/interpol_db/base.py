"""Declarative base shared by every table of the Interpol monitor database."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint and index names, so that Alembic migrations can refer to
# them by name. Pattern recommended in the SQLAlchemy documentation
# ("Configuring Constraint Naming Conventions").
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class of all ORM models; carries the shared metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
