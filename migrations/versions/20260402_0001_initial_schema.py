"""Initial schema."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260402_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("device_owner", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "audio_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("s3_bucket", sa.String(length=255), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="uploaded"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audio_chunks_status_started_at", "audio_chunks", ["status", "started_at"], unique=False)

    op.create_table(
        "voiceprints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("speaker_label", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="pyannote"),
        sa.Column("provider_voiceprint_id", sa.String(length=255), nullable=False),
        sa.Column("source_audio_key", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "transcript_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("audio_chunk_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("vendor", sa.String(length=50), nullable=False),
        sa.Column("vendor_segment_id", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("speaker_hint", sa.String(length=100), nullable=True),
        sa.Column("speaker_confidence", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["audio_chunk_id"], ["audio_chunks.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "canonical_utterances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("speaker_name", sa.String(length=100), nullable=True),
        sa.Column("speaker_confidence", sa.Float(), nullable=True),
        sa.Column("canonical_source_id", sa.String(length=100), nullable=True),
        sa.Column("processing_version", sa.String(length=50), nullable=False, server_default="v1"),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["canonical_source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_canonical_utterances_started_at", "canonical_utterances", ["started_at"], unique=False)
    op.create_index(
        "ix_canonical_utterances_search_vector",
        "canonical_utterances",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )

    op.create_table(
        "utterance_sources",
        sa.Column("canonical_utterance_id", sa.String(length=36), nullable=False),
        sa.Column("transcript_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("is_canonical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["canonical_utterance_id"], ["canonical_utterances.id"]),
        sa.ForeignKeyConstraint(["transcript_candidate_id"], ["transcript_candidates.id"]),
        sa.PrimaryKeyConstraint("canonical_utterance_id", "transcript_candidate_id"),
    )

    op.create_table(
        "agent_heartbeats",
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_upload_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("source_id"),
    )


def downgrade() -> None:
    op.drop_table("agent_heartbeats")
    op.drop_table("utterance_sources")
    op.drop_index("ix_canonical_utterances_search_vector", table_name="canonical_utterances")
    op.drop_index("ix_canonical_utterances_started_at", table_name="canonical_utterances")
    op.drop_table("canonical_utterances")
    op.drop_table("transcript_candidates")
    op.drop_table("voiceprints")
    op.drop_index("ix_audio_chunks_status_started_at", table_name="audio_chunks")
    op.drop_table("audio_chunks")
    op.drop_table("sources")
