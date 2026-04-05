"""Prefix ambient memory tables with aa_."""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260403_0002"
down_revision: str | None = "20260402_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


TABLE_RENAMES: tuple[tuple[str, str], ...] = (
    ("sources", "aa_sources"),
    ("audio_chunks", "aa_audio_chunks"),
    ("voiceprints", "aa_voiceprints"),
    ("transcript_candidates", "aa_transcript_candidates"),
    ("canonical_utterances", "aa_canonical_utterances"),
    ("utterance_sources", "aa_utterance_sources"),
    ("agent_heartbeats", "aa_agent_heartbeats"),
)


def upgrade() -> None:
    for old_name, new_name in TABLE_RENAMES:
        op.rename_table(old_name, new_name)


def downgrade() -> None:
    for old_name, new_name in reversed(TABLE_RENAMES):
        op.rename_table(new_name, old_name)
