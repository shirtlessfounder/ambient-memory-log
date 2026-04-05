"""Expand voiceprint provider payload storage."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260403_0003"
down_revision: str | None = "20260403_0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "aa_voiceprints",
        "provider_voiceprint_id",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "aa_voiceprints",
        "provider_voiceprint_id",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
