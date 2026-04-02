import pytest

from ambient_memory.capture.device_discovery import (
    UnknownAudioDeviceError,
    parse_avfoundation_list,
    select_audio_device,
)

SAMPLE_OUTPUT = """
[AVFoundation indev @ 0x123456] AVFoundation video devices:
[AVFoundation indev @ 0x123456] [0] FaceTime HD Camera
[AVFoundation indev @ 0x123456] AVFoundation audio devices:
[AVFoundation indev @ 0x123456] [0] Built-in Microphone
[AVFoundation indev @ 0x123456] [1] MacBook Pro Microphone
[AVFoundation indev @ 0x123456] [2] External USB Mic
"""


def test_parse_avfoundation_devices_extracts_audio_inputs():
    devices = parse_avfoundation_list(SAMPLE_OUTPUT)

    assert [device.name for device in devices] == [
        "Built-in Microphone",
        "MacBook Pro Microphone",
        "External USB Mic",
    ]


def test_parse_avfoundation_devices_preserves_indexes():
    devices = parse_avfoundation_list(SAMPLE_OUTPUT)

    assert [device.index for device in devices] == ["0", "1", "2"]


def test_select_audio_device_by_name_returns_matching_input():
    devices = parse_avfoundation_list(SAMPLE_OUTPUT)

    selected = select_audio_device(devices, "MacBook Pro Microphone")

    assert selected.index == "1"


def test_select_audio_device_by_index_returns_matching_input():
    devices = parse_avfoundation_list(SAMPLE_OUTPUT)

    selected = select_audio_device(devices, "2")

    assert selected.name == "External USB Mic"


def test_select_audio_device_raises_for_unknown_input():
    devices = parse_avfoundation_list(SAMPLE_OUTPUT)

    with pytest.raises(UnknownAudioDeviceError):
        select_audio_device(devices, "Missing Mic")
