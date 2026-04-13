from sqlalchemy import UniqueConstraint

from ambient_memory.models import Base


def test_models_expose_expected_tables():
    assert {
        "aa_sources",
        "aa_audio_chunks",
        "aa_voiceprints",
        "aa_transcript_candidates",
        "aa_canonical_utterances",
        "aa_canonical_utterance_enrichments",
        "aa_utterance_sources",
        "aa_agent_heartbeats",
    } <= set(Base.metadata.tables)


def test_canonical_utterances_has_search_index():
    table = Base.metadata.tables["aa_canonical_utterances"]

    assert "search_vector" in table.c


def test_canonical_utterances_preserve_raw_speaker_columns():
    table = Base.metadata.tables["aa_canonical_utterances"]

    assert {
        "raw_speaker_name",
        "raw_speaker_confidence",
    } <= set(table.c.keys())


def test_canonical_utterance_enrichments_has_required_columns_and_uniqueness():
    table = Base.metadata.tables["aa_canonical_utterance_enrichments"]

    assert {
        "id",
        "canonical_utterance_id",
        "resolver_vendor",
        "resolver_version",
        "resolved_speaker_name",
        "resolved_speaker_confidence",
        "cleaned_text",
        "cleaned_text_confidence",
        "identity_method",
        "identity_track_label",
        "identity_window_started_at",
        "identity_match_label",
        "identity_match_confidence",
        "identity_second_match_label",
        "identity_second_match_confidence",
        "transcript_method",
        "transcript_confidence",
        "resolution_notes",
        "created_at",
    } <= set(table.c.keys())

    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any(
        tuple(column.name for column in constraint.columns)
        == ("canonical_utterance_id", "resolver_vendor", "resolver_version")
        for constraint in unique_constraints
    )
