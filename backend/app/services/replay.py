import logging
import subprocess
import threading
import time
from pathlib import Path

from app.config import settings
from app.errors import ReplayError as _ReplayError

log = logging.getLogger(__name__)

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_stream = "cam1"

_STARTUP_CHECK_S = 0.5


ReplayError = _ReplayError


def _sample_path(stream: str = "cam1") -> Path:
    if settings.replay_source:
        return Path(settings.replay_source)
    per_stream = settings.media_path / f"{stream}.mp4"
    if per_stream.exists():
        return per_stream
    return settings.media_path / "sample.mp4"


def _drain(proc: subprocess.Popen) -> None:
    if proc.stderr is None:
        return
    for line in proc.stderr:
        log.warning("replay ffmpeg: %s", line.decode(errors="replace").rstrip())


def start_replay(stream: str = "cam1") -> dict:
    sample = _sample_path(stream)
    if not sample.exists():
        raise ReplayError(f"Sample video not found at {sample}")
    target = f"{settings.replay_rtsp_base}/{stream}"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-re",
        "-i",
        str(sample),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "30",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        target,
    ]

    with _lock:
        _terminate_locked()
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        threading.Thread(target=_drain, args=(proc,), daemon=True).start()
        global _proc, _stream
        _proc = proc
        _stream = stream

    time.sleep(_STARTUP_CHECK_S)
    if proc.poll() is not None and proc.returncode != 0:
        raise ReplayError("Could not start replay — is the go2rtc relay running?")
    return {"playing": proc.poll() is None, "stream": stream}


def replay_status() -> dict:
    with _lock:
        return {"playing": _proc is not None and _proc.poll() is None, "stream": _stream}


def _terminate_locked() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None


def stop_replay() -> None:
    with _lock:
        _terminate_locked()
