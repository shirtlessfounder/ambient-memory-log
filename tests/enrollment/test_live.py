from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ambient_memory.capture.device_discovery import AudioDevice
from ambient_memory.enrollment.live import (
    RECITATION_SCRIPT,
    build_live_record_command,
    run_live_voiceprint_enrollment,
)


def test_build_live_record_command_records_wav_from_avfoundation(tmp_path: Path) -> None:
    command = build_live_record_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        output_path=tmp_path / "sample.wav",
        ffmpeg_binary="ffmpeg",
    )

    assert command == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-f",
        "avfoundation",
        "-i",
        ":1",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(tmp_path / "sample.wav"),
    ]


def test_live_enrollment_rerecords_before_enrolling(tmp_path: Path) -> None:
    recordings_dir = tmp_path / "voiceprints"
    calls: dict[str, object] = {"commands": [], "prompts": [], "stopped": 0}

    class Settings:
        pyannote_api_key = "pyannote-key"
        database_url = "postgresql://db.example/app"
        database_ssl_root_cert = None

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            calls["api_key"] = api_key

        def enroll_voiceprint(self, *, label: str, audio_bytes: bytes, filename: str) -> str:
            calls["enroll"] = {
                "label": label,
                "audio_bytes": audio_bytes,
                "filename": filename,
            }
            return "vp-live-1"

    class FakeProcess:
        def __init__(self, output_path: Path) -> None:
            self.output_path = output_path
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True
            calls["stopped"] += 1
            self.output_path.write_bytes(f"audio-{calls['stopped']}".encode("utf-8"))

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("kill should not be called")

    def fake_start_recording(command: list[str]) -> FakeProcess:
        calls["commands"].append(command)
        return FakeProcess(Path(command[-1]))

    def fake_prompt(prompt: str) -> str:
        calls["prompts"].append(prompt)
        if len(calls["prompts"]) in {1, 2, 4, 5}:
            return ""
        return "r" if len(calls["prompts"]) == 3 else ""

    def fake_output(message: str) -> None:
        calls.setdefault("output", []).append(message)

    class FakeSession:
        def __init__(self) -> None:
            self.committed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            raise AssertionError("rollback should not be called")

        def close(self) -> None:
            return

    session = FakeSession()

    def fake_session_factory() -> FakeSession:
        return session

    def fake_upsert_voiceprint(
        current_session: FakeSession,
        *,
        speaker_label: str,
        provider_voiceprint_id: str,
        source_audio_key: str | None,
        provider: str = "pyannote",
    ):
        calls["saved"] = {
            "session": current_session,
            "speaker_label": speaker_label,
            "provider_voiceprint_id": provider_voiceprint_id,
            "source_audio_key": source_audio_key,
            "provider": provider,
        }
        return object(), True

    result = run_live_voiceprint_enrollment(
        label="  DYLAN  ",
        device_selection=None,
        ffmpeg_binary="ffmpeg",
        settings=Settings(),
        sample_dir=recordings_dir,
        list_devices=lambda ffmpeg_binary="ffmpeg": [AudioDevice(index="1", name="MacBook Pro Microphone")],
        choose_device=lambda devices, selection: devices[0],
        start_recording=fake_start_recording,
        prompt=fake_prompt,
        output=fake_output,
        now_factory=lambda: datetime(2026, 4, 3, 9, 0, 0, tzinfo=ZoneInfo("America/New_York")),
        client_factory=FakeClient,
        session_factory=fake_session_factory,
        upsert_voiceprint=fake_upsert_voiceprint,
    )

    assert result.speaker_label == "DYLAN"
    assert result.replaced_existing is True
    assert result.sample_path == recordings_dir / "dylan" / "20260403T090000-attempt02.wav"
    assert len(calls["commands"]) == 2
    assert calls["stopped"] == 2
    assert calls["commands"][0][-1].endswith("20260403T090000-attempt01.wav")
    assert calls["commands"][1][-1].endswith("20260403T090000-attempt02.wav")
    assert calls["api_key"] == "pyannote-key"
    assert calls["enroll"] == {
        "label": "DYLAN",
        "audio_bytes": b"audio-2",
        "filename": "20260403T090000-attempt02.wav",
    }
    assert calls["saved"] == {
        "session": session,
        "speaker_label": "DYLAN",
        "provider_voiceprint_id": "vp-live-1",
        "source_audio_key": str(recordings_dir / "dylan" / "20260403T090000-attempt02.wav"),
        "provider": "pyannote",
    }
    assert calls["prompts"] == [
        "Press Enter to start recording: ",
        "Recording. Press Enter to stop: ",
        "Press Enter to enroll, type r to re-record, or q to cancel: ",
        "Press Enter to start recording: ",
        "Recording. Press Enter to stop: ",
        "Press Enter to enroll, type r to re-record, or q to cancel: ",
    ]
    assert "Ambient Memory voiceprint enrollment. My name is DYLAN and I am speaking in my normal working voice." in (
        calls["output"]
    )
    assert any("ambient memory" in message.lower() for message in calls["output"])
    assert session.committed is True


def test_recitation_script_mentions_company_terms() -> None:
    joined = " ".join(RECITATION_SCRIPT).lower()

    assert "ambient memory" in joined
    assert "deepgram" in joined
    assert "postgres" in joined
