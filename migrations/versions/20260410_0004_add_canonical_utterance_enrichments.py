"""Add canonical utterance enrichment storage."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260410_0004"
down_revision: str | None = "20260403_0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aa_canonical_utterance_enrichments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canonical_utterance_id", sa.String(length=36), nullable=False),
        sa.Column("resolver_vendor", sa.String(length=50), nullable=False),
        sa.Column("resolver_version", sa.String(length=100), nullable=False),
        sa.Column("resolved_speaker_name", sa.String(length=100), nullable=False),
        sa.Column("resolved_speaker_confidence", sa.Float(), nullable=True),
        sa.Column("cleaned_text", sa.Text(), nullable=False),
        sa.Column("cleaned_text_confidence", sa.Float(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["canonical_utterance_id"], ["aa_canonical_utterances.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "canonical_utterance_id",
            "resolver_vendor",
            "resolver_version",
            name="uq_canonical_utterance_enrichments_resolver_version",
        ),
    )
    op.create_index(
        "ix_canonical_utterance_enrichments_canonical_utterance_id",
        "aa_canonical_utterance_enrichments",
        ["canonical_utterance_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_utterance_enrichments_canonical_utterance_id",
        table_name="aa_canonical_utterance_enrichments",
    )
    op.drop_table("aa_canonical_utterance_enrichments")
