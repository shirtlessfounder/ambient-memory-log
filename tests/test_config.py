import pytest

from ambient_memory.config import CaptureSettings, Settings


def test_settings_require_database_and_bucket(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Exception):
        Settings.model_validate({})


def test_capture_settings_default_backlog_capacity_is_larger_than_legacy_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAPTURE_MAX_BACKLOG_FILES", raising=False)

    settings = CaptureSettings()

    assert settings.capture_max_backlog_files > 32
