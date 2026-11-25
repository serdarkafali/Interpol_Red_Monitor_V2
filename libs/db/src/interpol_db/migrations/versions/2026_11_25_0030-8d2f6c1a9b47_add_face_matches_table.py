"""add face matches table

Revision ID: 8d2f6c1a9b47
Revises: 364114b36122
Create Date: 2026-11-25 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d2f6c1a9b47"
down_revision: str | Sequence[str] | None = "364114b36122"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "face_matches",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("first_face_id", sa.BigInteger(), nullable=False),
        sa.Column("second_face_id", sa.BigInteger(), nullable=False),
        sa.Column("distance", sa.Float(), nullable=False),
        sa.Column("same_notice", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "first_face_id < second_face_id", name=op.f("ck_face_matches_pair_ordered")
        ),
        sa.ForeignKeyConstraint(
            ["first_face_id"],
            ["faces.id"],
            name=op.f("fk_face_matches_first_face_id_faces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["second_face_id"],
            ["faces.id"],
            name=op.f("fk_face_matches_second_face_id_faces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_face_matches")),
        sa.UniqueConstraint(
            "first_face_id", "second_face_id", name=op.f("uq_face_matches_first_face_id")
        ),
    )
    op.create_index(
        op.f("ix_face_matches_second_face_id"), "face_matches", ["second_face_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_face_matches_second_face_id"), table_name="face_matches")
    op.drop_table("face_matches")
