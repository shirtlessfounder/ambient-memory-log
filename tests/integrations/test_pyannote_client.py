from ambient_memory.integrations.pyannote_client import PyannoteClient, PyannoteError, VoiceprintReference


class UploadRetryTransport:
    def __init__(self) -> None:
        self.upload_attempts = 0
        self.request_calls: list[tuple[str, str]] = []

    def request_json(self, method: str, url: str, **kwargs):
        self.request_calls.append((method, url))
        if url.endswith("/media/input"):
            return {"url": "https://upload.example.test/voiceprint"}
        if url.endswith("/identify"):
            return {"jobId": "job-1"}
        if url.endswith("/jobs/job-1"):
            return {"status": "succeeded", "output": {"voiceprints": []}}
        raise AssertionError(f"unexpected url: {url}")

    def upload_bytes(self, url: str, *, data: bytes, headers=None, timeout: float = 30.0) -> None:
        self.upload_attempts += 1
        if self.upload_attempts < 3:
            raise PyannoteError("pyannote upload failed: [Errno 54] Connection reset by peer")


class RequestRetryTransport:
    def __init__(self) -> None:
        self.media_request_attempts = 0
        self.upload_attempts = 0

    def request_json(self, method: str, url: str, **kwargs):
        if url.endswith("/media/input"):
            self.media_request_attempts += 1
            if self.media_request_attempts < 3:
                raise PyannoteError("pyannote request failed: temporary network issue")
            return {"url": "https://upload.example.test/voiceprint"}
        if url.endswith("/identify"):
            return {"jobId": "job-1"}
        if url.endswith("/jobs/job-1"):
            return {"status": "succeeded", "output": {"voiceprints": []}}
        raise AssertionError(f"unexpected url: {url}")

    def upload_bytes(self, url: str, *, data: bytes, headers=None, timeout: float = 30.0) -> None:
        self.upload_attempts += 1


def test_identify_speakers_retries_transient_upload_failure() -> None:
    transport = UploadRetryTransport()
    sleeps: list[float] = []
    client = PyannoteClient(
        api_key="secret",
        transport=transport,
        retry_attempts=3,
        retry_backoff_seconds=0.5,
        sleep=sleeps.append,
        poll_interval_seconds=0,
    )

    matches = client.identify_speakers(
        audio_bytes=b"audio-bytes",
        filename="sample.wav",
        voiceprints=[VoiceprintReference(label="Dylan", voiceprint="vp-1")],
    )

    assert matches == []
    assert transport.upload_attempts == 3
    assert sleeps == [0.5, 1.0]


def test_identify_speakers_retries_transient_media_request_failure() -> None:
    transport = RequestRetryTransport()
    sleeps: list[float] = []
    client = PyannoteClient(
        api_key="secret",
        transport=transport,
        retry_attempts=3,
        retry_backoff_seconds=0.5,
        sleep=sleeps.append,
        poll_interval_seconds=0,
    )

    matches = client.identify_speakers(
        audio_bytes=b"audio-bytes",
        filename="sample.wav",
        voiceprints=[VoiceprintReference(label="Dylan", voiceprint="vp-1")],
    )

    assert matches == []
    assert transport.media_request_attempts == 3
    assert transport.upload_attempts == 1
    assert sleeps == [0.5, 1.0]
