from pydantic import ValidationError
import pytest

from ambient_memory.config import RoomEnrichmentSettings, load_settings
from ambient_memory.pipeline.worker import load_worker_runtime_config


def _clear_worker_env(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    monkeypatch.delenv("ROOM_SPEAKER_ROSTER_PATH", raising=False)
    monkeypatch.delenv("ROOM_ASSEMBLY_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS", raising=False)
    monkeypatch.delenv("ROOM_MIN_SPEECH_SECONDS", raising=False)


def _clear_room_enrichment_env(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_AUDIO_TRANSCRIBE_MODEL", raising=False)
    monkeypatch.delenv("ROOM_TRACK_MIN_SPEECH_SECONDS", raising=False)
    monkeypatch.delenv("ROOM_TRACK_MATCH_THRESHOLD", raising=False)
    monkeypatch.delenv("ROOM_TRACK_MATCH_MARGIN", raising=False)


def test_worker_dry_run_config_loads_database_url_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://db.example/app\n", encoding="utf-8")
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=True)

    assert config.database_url == "postgresql://db.example/app"
    assert config.database_ssl_root_cert is None
    assert config.aws_region is None
    assert config.deepgram_api_key is None
    assert config.pyannote_api_key is None
    assert config.assemblyai_api_key is None
    assert config.room_speaker_roster_path is None
    assert config.room_assembly_window_seconds == 600
    assert config.room_assembly_idle_flush_seconds == 120


def test_worker_dry_run_config_loads_database_url_from_explicit_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text("DATABASE_URL=postgresql://db.example/worker\n", encoding="utf-8")
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=True, env_file=".env.worker")

    assert config.database_url == "postgresql://db.example/worker"
    assert config.assemblyai_api_key is None
    assert config.room_speaker_roster_path is None
    assert config.room_assembly_window_seconds == 600
    assert config.room_assembly_idle_flush_seconds == 120


def test_worker_runtime_config_requires_assemblyai_api_key_for_non_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    with pytest.raises(RuntimeError, match="ASSEMBLYAI_API_KEY"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")


def test_worker_runtime_config_loads_room_settings_for_non_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
                "ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json",
                "ROOM_ASSEMBLY_WINDOW_SECONDS=900",
                "ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS=180",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=False, env_file=".env.worker")

    assert config.room_speaker_roster_path == "./config/room-speakers.json"
    assert config.room_assembly_window_seconds == 900
    assert config.room_assembly_idle_flush_seconds == 180


def test_worker_runtime_config_requires_room_speaker_roster_path_for_non_dry_run(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    with pytest.raises(RuntimeError, match="ROOM_SPEAKER_ROSTER_PATH"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")


def test_worker_runtime_config_rejects_non_positive_room_window_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
                "ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json",
                "ROOM_ASSEMBLY_WINDOW_SECONDS=0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    with pytest.raises(Exception, match="ROOM_ASSEMBLY_WINDOW_SECONDS|room_assembly_window_seconds"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")


def test_worker_runtime_config_defaults_room_min_speech_seconds_in_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://db.example/app\n", encoding="utf-8")
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=True)

    assert config.room_min_speech_seconds == 20.0


def test_worker_runtime_config_loads_room_min_speech_seconds_for_non_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
                "ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json",
                "ROOM_MIN_SPEECH_SECONDS=45",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=False, env_file=".env.worker")

    assert config.room_min_speech_seconds == 45.0


def test_worker_runtime_config_rejects_non_positive_room_min_speech_seconds(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
                "ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json",
                "ROOM_MIN_SPEECH_SECONDS=0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    with pytest.raises(Exception, match="ROOM_MIN_SPEECH_SECONDS|room_min_speech_seconds"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")


def test_room_enrichment_settings_load_room_v2_defaults_from_explicit_env_file(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.room-enrichment").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/room-enrichment",
                "OPENAI_API_KEY=openai-secret",
                "AWS_REGION=us-east-1",
                "PYANNOTE_API_KEY=pyannote-secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_room_enrichment_env(monkeypatch)

    settings = load_settings(RoomEnrichmentSettings, env_file=".env.room-enrichment")

    assert settings.database_url == "postgresql://db.example/room-enrichment"
    assert settings.openai_api_key == "openai-secret"
    assert settings.aws_region == "us-east-1"
    assert settings.pyannote_api_key == "pyannote-secret"
    assert settings.openai_audio_transcribe_model == "gpt-4o-transcribe-diarize"
    assert settings.room_track_min_speech_seconds == 8.0
    assert settings.room_track_match_threshold == 0.75
    assert settings.room_track_match_margin == 0.15


def test_room_enrichment_settings_allow_room_v2_overrides_from_explicit_env_file(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.room-enrichment").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/room-enrichment",
                "OPENAI_API_KEY=openai-secret",
                "AWS_REGION=us-west-2",
                "PYANNOTE_API_KEY=pyannote-secret",
                "OPENAI_AUDIO_TRANSCRIBE_MODEL=gpt-4o-transcribe",
                "ROOM_TRACK_MIN_SPEECH_SECONDS=12.5",
                "ROOM_TRACK_MATCH_THRESHOLD=0.8",
                "ROOM_TRACK_MATCH_MARGIN=0.2",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_room_enrichment_env(monkeypatch)

    settings = load_settings(RoomEnrichmentSettings, env_file=".env.room-enrichment")

    assert settings.aws_region == "us-west-2"
    assert settings.openai_audio_transcribe_model == "gpt-4o-transcribe"
    assert settings.room_track_min_speech_seconds == 12.5
    assert settings.room_track_match_threshold == 0.8
    assert settings.room_track_match_margin == 0.2


@pytest.mark.parametrize(
    ("field_name", "raw_value"),
    (
        ("ROOM_TRACK_MIN_SPEECH_SECONDS", "0"),
        ("ROOM_TRACK_MATCH_THRESHOLD", "1.1"),
        ("ROOM_TRACK_MATCH_MARGIN", "-0.01"),
    ),
)
def test_room_enrichment_settings_reject_invalid_room_v2_thresholds(
    tmp_path,
    monkeypatch,
    field_name: str,
    raw_value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.room-enrichment").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/room-enrichment",
                "OPENAI_API_KEY=openai-secret",
                "AWS_REGION=us-east-1",
                "PYANNOTE_API_KEY=pyannote-secret",
                f"{field_name}={raw_value}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_room_enrichment_env(monkeypatch)

    with pytest.raises(ValidationError, match=field_name):
        load_settings(RoomEnrichmentSettings, env_file=".env.room-enrichment")
