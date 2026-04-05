from sqlalchemy import Text, create_engine, select
from sqlalchemy.orm import Session

from ambient_memory.db import normalize_speaker_label, upsert_voiceprint
from ambient_memory.models import Voiceprint


def test_normalize_speaker_label_ignores_case_and_outer_whitespace() -> None:
    assert normalize_speaker_label("  DyLaN  ") == "dylan"


def test_upsert_voiceprint_replaces_existing_case_insensitive_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Voiceprint.__table__.create(bind=engine)

    with Session(engine) as session:
        session.add(
            Voiceprint(
                speaker_label="Dylan",
                provider="pyannote",
                provider_voiceprint_id="vp-old",
                source_audio_key="voiceprints/dylan-old.wav",
            )
        )
        session.commit()

        row, replaced_existing = upsert_voiceprint(
            session,
            speaker_label="  dYLaN ",
            provider_voiceprint_id="vp-new",
            source_audio_key="voiceprints/dylan-new.wav",
        )
        session.commit()

        stored = session.scalars(select(Voiceprint)).all()

    assert replaced_existing is True
    assert row.speaker_label == "dYLaN"
    assert row.provider_voiceprint_id == "vp-new"
    assert row.source_audio_key == "voiceprints/dylan-new.wav"
    assert len(stored) == 1


def test_voiceprint_provider_id_uses_text_storage() -> None:
    assert isinstance(Voiceprint.__table__.c.provider_voiceprint_id.type, Text)
