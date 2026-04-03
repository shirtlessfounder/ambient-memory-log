from pydantic import ValidationError
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

    assert settings.capture_max_backlog_files == 2048


@pytest.mark.parametrize("raw_value", ["0", "-1"])
def test_capture_settings_rejects_non_positive_backlog_capacity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    raw_value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAPTURE_MAX_BACKLOG_FILES", raw_value)

    with pytest.raises(ValidationError) as exc_info:
        CaptureSettings()

    errors = exc_info.value.errors(include_url=False)

    assert len(errors) == 1
    assert errors[0]["type"] == "greater_than"
    assert errors[0]["loc"] == ("CAPTURE_MAX_BACKLOG_FILES",)
    assert errors[0]["msg"] == "Input should be greater than 0"
    assert errors[0]["ctx"] == {"gt": 0}
