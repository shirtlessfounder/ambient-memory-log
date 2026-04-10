import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from pathlib import Path
import threading
import time
from typing import TextIO

from typer import Argument, Context, Exit, Option, Typer
from typer.testing import CliRunner

from ambient_memory.api.app import run_api_server
from ambient_memory.capture.agent import list_local_audio_devices, run_capture_agent
from ambient_memory.config import DatabaseSettings, EnrollmentSettings, load_settings
from ambient_memory.db import count_audio_chunks_for_source, create_voiceprint, session_scope
from ambient_memory.enrollment.live import run_live_voiceprint_enrollment
from ambient_memory.importing.recordings import derive_source_id, run_recording_import
from ambient_memory.integrations.pyannote_client import PyannoteClient
from ambient_memory.pipeline.room_enrichment import (
    DEFAULT_ROOM_ENRICHMENT_RESOLVER_VERSION,
    run_room_enrichment,
)
from ambient_memory.pipeline.worker import run_worker_loop, run_worker_once


class HelpTyper(Typer):
    def get_help(self) -> str:
        runner = CliRunner()
        result = runner.invoke(self, ["--help"])
        return result.output


app = HelpTyper(help="Ambient memory log CLI.")
agent_app = Typer(help="Capture agent commands.")
worker_app = Typer(help="Pipeline worker commands.")
api_app = Typer(help="Read API commands.", invoke_without_command=True)
enroll_app = Typer(help="Enrollment commands.")

app.add_typer(agent_app, name="agent")
app.add_typer(worker_app, name="worker")
app.add_typer(api_app, name="api")
app.add_typer(enroll_app, name="enroll")


WORKER_ESTIMATE_LOW_SECONDS_PER_CHUNK = 7.0
WORKER_ESTIMATE_HIGH_SECONDS_PER_CHUNK = 13.5
ENROLLMENT_ENV_FILE = ".env.teammate"


@dataclass(frozen=True, slots=True)
class WorkerRuntimeEstimate:
    duration_text: str
    completion_text: str


@dataclass(frozen=True, slots=True)
class DualCaptureChildSpec:
    role: str
    command: tuple[str, ...]


def _render_worker_run_once_result(result: object, *, dry_run: bool) -> None:
    if dry_run:
        print(f"Pending uploaded chunks: {result.pending_chunks} across {result.windows} window(s)")
        return

    print(
        "Processed "
        f"{result.processed_chunks} chunk(s) across {result.windows} window(s); "
        f"failed {result.failed_chunks}"
    )


def _render_room_enrichment_result(result: object, *, dry_run: bool) -> None:
    if dry_run:
        print(
            "Dry run: "
            f"{result.utterances} utterance(s) across {result.windows} window(s) "
            f"would be processed for {result.source_id} over the last {result.hours}h"
        )
        return

    print(
        f"Created {result.created} enrichment row(s) "
        f"for {result.utterances} utterance(s) across {result.windows} window(s)"
    )


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _estimate_worker_runtime_summary(chunk_count: int, *, now: datetime | None = None) -> WorkerRuntimeEstimate:
    reference_time = now or _now_local()
    low_minutes = _normalize_estimate_minutes(
        chunk_count * WORKER_ESTIMATE_LOW_SECONDS_PER_CHUNK / 60,
        round_up=False,
    )
    high_minutes = _normalize_estimate_minutes(
        chunk_count * WORKER_ESTIMATE_HIGH_SECONDS_PER_CHUNK / 60,
        round_up=True,
    )
    high_minutes = max(low_minutes, high_minutes)

    if low_minutes == high_minutes:
        duration_text = f"~{low_minutes} minute" if low_minutes == 1 else f"~{low_minutes} minutes"
    else:
        duration_text = f"~{low_minutes}-{high_minutes} minutes"

    low_eta = reference_time + timedelta(minutes=low_minutes)
    high_eta = reference_time + timedelta(minutes=high_minutes)
    completion_text = _format_completion_window(low_eta, high_eta)

    return WorkerRuntimeEstimate(duration_text=duration_text, completion_text=completion_text)


def _normalize_estimate_minutes(value: float, *, round_up: bool) -> int:
    if value <= 1:
        return 1
    if value < 10:
        rounded = math.ceil(value) if round_up else math.floor(value)
        return max(1, rounded)

    if round_up:
        return max(5, math.ceil(value / 5) * 5)
    return max(5, math.floor(value / 5) * 5)


def _format_completion_window(low_eta: datetime, high_eta: datetime) -> str:
    timezone_label = low_eta.tzname() or high_eta.tzname() or ""
    if low_eta == high_eta:
        base = f"~{_format_clock(low_eta)}"
    else:
        base = f"~{_format_clock(low_eta)} to {_format_clock(high_eta)}"

    if timezone_label:
        return f"{base} {timezone_label}"
    return base


def _format_clock(value: datetime) -> str:
    hour = value.strftime("%I").lstrip("0") or "12"
    return f"{hour}:{value.strftime('%M %p')}"


def _should_run_worker_after_import(prompt=input) -> bool:
    while True:
        try:
            response = prompt("Run worker now? [Y/n]: ").strip().lower()
        except EOFError:
            return False

        if response in {"", "y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False

        print("Please answer y or n.")


def _count_existing_chunks_for_source_id(source_id: str) -> int:
    with session_scope(DatabaseSettings()) as session:
        return count_audio_chunks_for_source(session, source_id=source_id)


def _append_existing_source_message(source_id: str, existing_chunks: int) -> str:
    return f"Source id {source_id} already has {existing_chunks} chunk(s)."


def _should_append_existing_source_id(
    source_id: str,
    existing_chunks: int,
    *,
    prompt=input,
) -> bool:
    prompt_text = f"{_append_existing_source_message(source_id, existing_chunks)} Continue and append? [y/N]: "
    while True:
        try:
            response = prompt(prompt_text).strip().lower()
        except EOFError:
            return False

        if response in {"", "n", "no"}:
            return False
        if response in {"y", "yes"}:
            return True

        print("Please answer y or n.")


def _stream_process_output(role: str, label: str, stream: TextIO, *, output: TextIO) -> None:
    for line in iter(stream.readline, ""):
        message = line.rstrip()
        if message:
            print(f"[{role} {label}] {message}", file=output, flush=True)
    stream.close()


def _start_process_output_threads(role: str, process: subprocess.Popen[str]) -> list[threading.Thread]:
    threads: list[threading.Thread] = []

    if process.stdout is not None:
        stdout_thread = threading.Thread(
            target=_stream_process_output,
            args=(role, "stdout", process.stdout),
            kwargs={"output": sys.stdout},
            daemon=True,
        )
        stdout_thread.start()
        threads.append(stdout_thread)

    if process.stderr is not None:
        stderr_thread = threading.Thread(
            target=_stream_process_output,
            args=(role, "stderr", process.stderr),
            kwargs={"output": sys.stderr},
            daemon=True,
        )
        stderr_thread.start()
        threads.append(stderr_thread)

    return threads


def _stop_child_processes(
    processes: list[subprocess.Popen[str]],
    *,
    terminate_timeout_seconds: float = 5.0,
) -> None:
    for process in processes:
        if process.poll() is not None:
            continue
        _signal_process_tree(process, signal.SIGTERM)

    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=terminate_timeout_seconds)
        except subprocess.TimeoutExpired:
            _signal_process_tree(process, signal.SIGKILL)
            process.wait()


def _signal_process_tree(process: subprocess.Popen[str], signum: int) -> None:
    try:
        process_group_id = os.getpgid(process.pid)
    except (AttributeError, ProcessLookupError):
        process_group_id = None

    if process_group_id is not None:
        try:
            os.killpg(process_group_id, signum)
            return
        except ProcessLookupError:
            return

    if signum == signal.SIGKILL:
        process.kill()
    else:
        process.terminate()


def _run_dual_capture(
    *,
    child_specs: tuple[DualCaptureChildSpec, ...],
    cwd: Path,
    popen_factory=subprocess.Popen,
    sleep_fn=time.sleep,
    poll_seconds: float = 0.1,
) -> int:
    processes: list[tuple[DualCaptureChildSpec, subprocess.Popen[str]]] = []
    threads: list[threading.Thread] = []
    requested_signal: int | None = None
    previous_handlers: list[tuple[int, object]] = []

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal requested_signal
        requested_signal = signum

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers.append((signum, signal.getsignal(signum)))
        signal.signal(signum, handle_signal)

    try:
        for child_spec in child_specs:
            try:
                process = popen_factory(
                    child_spec.command,
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
            except OSError as error:
                print(f"[{child_spec.role} supervisor] failed to start: {error}", file=sys.stderr)
                _stop_child_processes([running_process for _, running_process in processes])
                return 1

            processes.append((child_spec, process))
            threads.extend(_start_process_output_threads(child_spec.role, process))

        while True:
            if requested_signal is not None:
                _stop_child_processes([process for _, process in processes])
                return 128 + requested_signal

            for index, (child_spec, process) in enumerate(processes):
                return_code = process.poll()
                if return_code is None:
                    continue

                print(
                    f"[{child_spec.role} supervisor] child exited unexpectedly with code {return_code}",
                    file=sys.stderr,
                )
                sibling_processes = [other_process for position, (_, other_process) in enumerate(processes) if position != index]
                _stop_child_processes(sibling_processes)
                return return_code if return_code != 0 else 1

            sleep_fn(poll_seconds)
    except KeyboardInterrupt:
        _stop_child_processes([process for _, process in processes])
        return 130
    finally:
        for signum, previous_handler in previous_handlers:
            signal.signal(signum, previous_handler)
        for thread in threads:
            thread.join(timeout=1.0)


def _validate_dual_capture_env_files(cwd: Path) -> bool:
    required_env_files = (".env.teammate", ".env.room-mic")
    missing = [env_file for env_file in required_env_files if not (cwd / env_file).is_file()]
    for env_file in missing:
        print(f"missing {env_file} in {cwd}", file=sys.stderr)
    return not missing


@app.command("import-recording")
def import_recording(
    recording_path: Path = Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to a prerecorded meeting audio file.",
    ),
    start: str = Option(..., "--start", help='Local meeting start time in "YYYY-MM-DD HH:MM" format.'),
    source_id: str | None = Option(None, "--source-id", help="Optional source id override for imported chunks."),
    allow_existing_source_id: bool = Option(
        False,
        "--allow-existing-source-id",
        help="Allow appending new chunks to an existing source id.",
    ),
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
) -> None:
    """Split and upload a prerecorded meeting recording."""
    resolved_source_id = source_id or derive_source_id(recording_path)
    if not allow_existing_source_id:
        existing_chunks = _count_existing_chunks_for_source_id(resolved_source_id)
        if existing_chunks > 0:
            if not _is_interactive_terminal():
                print(
                    f"{_append_existing_source_message(resolved_source_id, existing_chunks)} "
                    "Re-run with --allow-existing-source-id to append."
                )
                raise Exit(code=1)
            if not _should_append_existing_source_id(resolved_source_id, existing_chunks):
                print("Import cancelled.")
                raise Exit(code=1)

    result = run_recording_import(
        recording_path=recording_path,
        start=start,
        source_id=resolved_source_id,
        ffmpeg_binary=ffmpeg_binary,
    )
    print(f"Imported {result.uploaded} chunk(s) as {result.source_id}")

    if _is_interactive_terminal() and result.uploaded > 0:
        estimate = _estimate_worker_runtime_summary(result.uploaded, now=_now_local())
        print(f"Estimated worker time: {estimate.duration_text}")
        print(f"Estimated completion: {estimate.completion_text}")

    if _is_interactive_terminal() and result.uploaded > 0 and _should_run_worker_after_import():
        worker_result = run_worker_once(dry_run=False)
        _render_worker_run_once_result(worker_result, dry_run=False)


@app.command("list-devices")
def list_devices(
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
) -> None:
    """List local capture devices."""
    for device in list_local_audio_devices(ffmpeg_binary=ffmpeg_binary):
        print(f"{device.index}: {device.name}")


@agent_app.command("run")
def agent_run(
    dry_run: bool = Option(False, "--dry-run", help="Log configuration without recording or uploading."),
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
    device_selection: str | None = Option(None, "--device", help="Audio device name or index."),
) -> None:
    """Run the local capture agent."""
    run_capture_agent(
        dry_run=dry_run,
        ffmpeg_binary=ffmpeg_binary,
        device_selection=device_selection,
        env_file=None,
    )


@app.command("start-teammate")
def start_teammate(
    dry_run: bool = Option(False, "--dry-run", help="Log configuration without recording or uploading."),
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
    device_selection: str | None = Option(None, "--device", help="Audio device name or index."),
) -> None:
    """Start teammate laptop capture using .env.teammate."""
    run_capture_agent(
        dry_run=dry_run,
        ffmpeg_binary=ffmpeg_binary,
        device_selection=device_selection,
        env_file=".env.teammate",
    )


@app.command("start-room-mic")
def start_room_mic(
    dry_run: bool = Option(False, "--dry-run", help="Log configuration without recording or uploading."),
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
    device_selection: str | None = Option(None, "--device", help="Audio device name or index."),
) -> None:
    """Start room microphone capture using .env.room-mic."""
    run_capture_agent(
        dry_run=dry_run,
        ffmpeg_binary=ffmpeg_binary,
        device_selection=device_selection,
        env_file=".env.room-mic",
    )


@app.command("start-dual-capture")
def start_dual_capture() -> None:
    """Start teammate and room-mic capture together."""
    cwd = Path.cwd()
    if not _validate_dual_capture_env_files(cwd):
        raise Exit(code=1)

    exit_code = _run_dual_capture(
        child_specs=(
            DualCaptureChildSpec("teammate", ("uv", "run", "ambient-memory", "start-teammate")),
            DualCaptureChildSpec("room-mic", ("uv", "run", "ambient-memory", "start-room-mic")),
        ),
        cwd=cwd,
    )
    if exit_code != 0:
        raise Exit(code=exit_code)


@worker_app.command("run-once")
def worker_run_once_command(
    dry_run: bool = Option(False, "--dry-run", help="Report pending uploaded chunks without mutating data."),
) -> None:
    """Process uploaded audio chunks once."""
    result = run_worker_once(dry_run=dry_run)
    _render_worker_run_once_result(result, dry_run=dry_run)


@worker_app.command("run")
def worker_run(
    poll_seconds: float = Option(5.0, "--poll-seconds", min=0.1, help="Seconds to wait between worker polls."),
) -> None:
    """Poll and process uploaded audio chunks continuously."""
    run_worker_loop(poll_seconds=poll_seconds, env_file=None)


@app.command("start-worker")
def start_worker(
    poll_seconds: float = Option(5.0, "--poll-seconds", min=0.1, help="Seconds to wait between worker polls."),
) -> None:
    """Start the worker using .env.worker."""
    run_worker_loop(poll_seconds=poll_seconds, env_file=".env.worker")


@app.command("enrich-room")
def enrich_room(
    hours: int = Option(4, "--hours", min=1, help="Only enrich canonical utterances from the last N hours."),
    source_id: str = Option("room-1", "--source-id", help="Canonical source id to enrich."),
    resolver_version: str = Option(
        DEFAULT_ROOM_ENRICHMENT_RESOLVER_VERSION,
        "--resolver-version",
        help="Resolver version label stored with inferred enrichment rows; same-version reruns stay idempotent.",
    ),
    dry_run: bool = Option(
        False,
        "--dry-run",
        help="Report recent room-v2 scope without writing inferred rows; raw canonical rows remain unchanged.",
    ),
) -> None:
    """Run room v2 audio-track identity and audio-aware retranscription for recent room canonical utterances."""
    result = run_room_enrichment(
        hours=hours,
        source_id=source_id,
        resolver_version=resolver_version,
        dry_run=dry_run,
    )
    _render_room_enrichment_result(result, dry_run=dry_run)


def _start_api_server(
    host: str | None = Option(None, "--host", help="Host interface to bind the API server to."),
    port: int | None = Option(None, "--port", min=1, max=65535, help="Port to bind the API server to."),
) -> None:
    run_api_server(host=host, port=port, env_file=None)


@api_app.callback()
def api_callback(
    ctx: Context,
    host: str | None = Option(None, "--host", help="Host interface to bind the API server to."),
    port: int | None = Option(None, "--port", min=1, max=65535, help="Port to bind the API server to."),
) -> None:
    """Run the read API."""
    if ctx.invoked_subcommand is None:
        _start_api_server(host=host, port=port)


@api_app.command("run")
def api_run(
    host: str | None = Option(None, "--host", help="Host interface to bind the API server to."),
    port: int | None = Option(None, "--port", min=1, max=65535, help="Port to bind the API server to."),
) -> None:
    """Run the read API."""
    _start_api_server(host=host, port=port)


@app.command("start-api")
def start_api(
    host: str | None = Option(None, "--host", help="Host interface to bind the API server to."),
    port: int | None = Option(None, "--port", min=1, max=65535, help="Port to bind the API server to."),
) -> None:
    """Start the read API using .env.api."""
    run_api_server(host=host, port=port, env_file=".env.api")


@enroll_app.command("voiceprint")
def enroll_voiceprint(
    label: str = Option(..., "--label", help="Speaker label to associate with the voiceprint."),
    audio: Path = Option(
        ...,
        "--audio",
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to a single-speaker enrollment audio file.",
    ),
) -> None:
    """Enroll a reusable speaker voiceprint."""
    settings = load_settings(EnrollmentSettings, env_file=ENROLLMENT_ENV_FILE)
    client = PyannoteClient(api_key=settings.pyannote_api_key)
    voiceprint_id = client.enroll_voiceprint(
        label=label,
        audio_bytes=audio.read_bytes(),
        filename=audio.name,
    )

    with session_scope(settings) as session:
        create_voiceprint(
            session,
            speaker_label=label,
            provider_voiceprint_id=voiceprint_id,
            source_audio_key=str(audio),
        )

    print(f"Created voiceprint for {label}")


@enroll_app.command("voiceprint-live")
def enroll_voiceprint_live(
    label: str = Option(..., "--label", help="Speaker label to associate with the voiceprint."),
    device_selection: str | None = Option(None, "--device", help="Optional audio device name or index override."),
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
) -> None:
    """Record, review, and enroll a live speaker voiceprint."""
    settings = load_settings(EnrollmentSettings, env_file=ENROLLMENT_ENV_FILE)
    result = run_live_voiceprint_enrollment(
        label=label,
        device_selection=device_selection,
        ffmpeg_binary=ffmpeg_binary,
        settings=settings,
    )
    verb = "Updated" if result.replaced_existing else "Created"
    print(f"{verb} voiceprint for {result.speaker_label}")
    print(f"Saved sample to {result.sample_path}")


def main() -> None:
    app()
