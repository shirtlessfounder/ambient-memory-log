from ambient_memory.models import Base


def test_models_expose_expected_tables():
    assert {
        "sources",
        "audio_chunks",
        "voiceprints",
        "transcript_candidates",
        "canonical_utterances",
        "utterance_sources",
        "agent_heartbeats",
    } <= set(Base.metadata.tables)


def test_canonical_utterances_has_search_index():
    table = Base.metadata.tables["canonical_utterances"]

    assert "search_vector" in table.c
