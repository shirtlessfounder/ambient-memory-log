"""Add raw speaker preservation fields to canonical utterances."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260413_0006"
down_revision: str | None = "20260410_0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "aa_canonical_utterances",
        sa.Column("raw_speaker_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterances",
        sa.Column("raw_speaker_confidence", sa.Float(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE aa_canonical_utterances
            SET raw_speaker_name = speaker_name,
                raw_speaker_confidence = speaker_confidence
            """
        )
    )


def downgrade() -> None:
    op.drop_column("aa_canonical_utterances", "raw_speaker_confidence")
    op.drop_column("aa_canonical_utterances", "raw_speaker_name")
