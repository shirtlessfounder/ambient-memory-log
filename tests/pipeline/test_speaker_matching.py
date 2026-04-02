from ambient_memory.pipeline.speaker_matching import SpeakerMatch, choose_speaker


def test_local_source_biases_to_device_owner_when_confident() -> None:
    match = choose_speaker(source_owner="dylan", pyannote_match="dylan", confidence=81)

    assert match == SpeakerMatch(
        speaker_name="dylan",
        confidence=0.91,
        source_owner="dylan",
        pyannote_match="dylan",
        pyannote_confidence=0.81,
    )


def test_low_confidence_room_segment_remains_uncertain() -> None:
    match = choose_speaker(source_owner=None, pyannote_match="dylan", confidence=32)

    assert match == SpeakerMatch(
        speaker_name=None,
        confidence=0.32,
        source_owner=None,
        pyannote_match="dylan",
        pyannote_confidence=0.32,
    )


def test_conflicting_local_owner_and_pyannote_match_stays_unnamed() -> None:
    match = choose_speaker(source_owner="dylan", pyannote_match="sam", confidence=88)

    assert match == SpeakerMatch(
        speaker_name=None,
        confidence=0.58,
        source_owner="dylan",
        pyannote_match="sam",
        pyannote_confidence=0.88,
    )
