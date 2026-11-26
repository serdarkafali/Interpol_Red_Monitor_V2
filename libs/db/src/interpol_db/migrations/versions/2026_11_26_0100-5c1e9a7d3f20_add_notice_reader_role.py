"""add notice reader role

A role that can only read, for the web interface (ADR 0013). It cannot log in; the login
user is created by ``python -m interpol_db.reader_user`` in the migrate step, because its
password is a secret and must not be written here.

Revision ID: 5c1e9a7d3f20
Revises: 8d2f6c1a9b47
Create Date: 2026-11-26 01:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c1e9a7d3f20"
down_revision: str | Sequence[str] | None = "8d2f6c1a9b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Roles belong to the whole PostgreSQL server, not to one database, so the role may
    # already exist (another database on the same server, or an earlier run).
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'notice_reader') THEN
                CREATE ROLE notice_reader NOLOGIN;
            END IF;
        END $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO notice_reader")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO notice_reader")
    # Tables added by later migrations become readable too, without granting each one.
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO notice_reader")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM notice_reader"
    )
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM notice_reader")
    op.execute("REVOKE USAGE ON SCHEMA public FROM notice_reader")
    # The role stays if it still has rights in another database on the same server.
    op.execute(
        """
        DO $$ BEGIN
            DROP ROLE IF EXISTS notice_reader;
        EXCEPTION WHEN dependent_objects_still_exist THEN
            NULL;
        END $$
        """
    )
