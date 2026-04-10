"""Extend canonical utterance enrichments for room v2."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260410_0005"
down_revision: str | None = "20260410_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("identity_method", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("identity_track_label", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("identity_window_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("identity_match_label", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("identity_match_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("identity_second_match_label", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("identity_second_match_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("transcript_method", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "aa_canonical_utterance_enrichments",
        sa.Column("transcript_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aa_canonical_utterance_enrichments", "transcript_confidence")
    op.drop_column("aa_canonical_utterance_enrichments", "transcript_method")
    op.drop_column("aa_canonical_utterance_enrichments", "identity_second_match_confidence")
    op.drop_column("aa_canonical_utterance_enrichments", "identity_second_match_label")
    op.drop_column("aa_canonical_utterance_enrichments", "identity_match_confidence")
    op.drop_column("aa_canonical_utterance_enrichments", "identity_match_label")
    op.drop_column("aa_canonical_utterance_enrichments", "identity_window_started_at")
    op.drop_column("aa_canonical_utterance_enrichments", "identity_track_label")
    op.drop_column("aa_canonical_utterance_enrichments", "identity_method")
