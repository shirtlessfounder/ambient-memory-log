from dataclasses import dataclass
import re


DEVICE_LINE_PATTERN = re.compile(r"\[\d+\]\s")


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: str
    name: str


class UnknownAudioDeviceError(ValueError):
    pass


def parse_avfoundation_list(output: str) -> list[AudioDevice]:
    devices: list[AudioDevice] = []
    in_audio_section = False

    for raw_line in output.splitlines():
        line = raw_line.strip()

        if "AVFoundation audio devices:" in line:
            in_audio_section = True
            continue

        if "AVFoundation video devices:" in line:
            in_audio_section = False
            continue

        if not in_audio_section:
            continue

        match = re.search(r"\[(\d+)\]\s(.+)$", line)
        if match and DEVICE_LINE_PATTERN.search(line):
            devices.append(AudioDevice(index=match.group(1), name=match.group(2)))

    return devices


def select_audio_device(
    devices: list[AudioDevice],
    selection: str | None,
) -> AudioDevice:
    if selection is None:
        if len(devices) == 1:
            return devices[0]
        raise UnknownAudioDeviceError("audio device selection is required")

    for device in devices:
        if selection in {device.index, device.name}:
            return device

    available = ", ".join(f"{device.index}:{device.name}" for device in devices)
    raise UnknownAudioDeviceError(
        f"unknown audio device {selection!r}; available devices: {available}"
    )
