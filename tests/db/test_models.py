from ambient_memory.models import Base


def test_models_expose_expected_tables():
    assert {
        "aa_sources",
        "aa_audio_chunks",
        "aa_voiceprints",
        "aa_transcript_candidates",
        "aa_canonical_utterances",
        "aa_utterance_sources",
        "aa_agent_heartbeats",
    } <= set(Base.metadata.tables)


def test_canonical_utterances_has_search_index():
    table = Base.metadata.tables["aa_canonical_utterances"]

    assert "search_vector" in table.c
