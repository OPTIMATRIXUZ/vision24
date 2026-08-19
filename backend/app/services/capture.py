import contextlib
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

import cv2

from app.config import settings
from app.errors import CaptureError as _CaptureError

log = logging.getLogger(__name__)

_active_lock = threading.Lock()
_active: dict[str, subprocess.Popen] = {}
_stopped: set[str] = set()

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

CAPTURE_DIR = settings.media_path / "captures"
PROBE_TIMEOUT_S = 20
SOCKET_TIMEOUT_US = "15000000"

MIN_DURATION_S = 10
MAX_DURATION_S = 300
DEFAULT_DURATION_S = 120


CaptureError = _CaptureError


def capture_path(camera_id) -> Path:
    return CAPTURE_DIR / f"{camera_id}.mp4"


def _scrub(text: str, rtsp_url: str) -> str:
    scrubbed = text.replace(rtsp_url, "rtsp://<camera>")
    return re.sub(r"rtsp://[^\s'\"]+", "rtsp://<camera>", scrubbed)


def probe_snapshot(rtsp_url: str) -> bytes:
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        SOCKET_TIMEOUT_US,
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise CaptureError("The camera did not respond within 20 seconds.") from None
    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode(errors="replace")[-300:]
        raise CaptureError(f"Could not read from camera: {_scrub(stderr, rtsp_url)}")
    return result.stdout


def file_snapshot(path: str) -> bytes:
    cap = cv2.VideoCapture(path)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise CaptureError("Could not read a frame from the video file")
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise CaptureError("Could not encode frame")
    return jpeg.tobytes()


def go2rtc_frame(src: str) -> bytes:
    import httpx

    from app.config import settings

    try:
        resp = httpx.get(
            f"{settings.go2rtc_base_url}/api/frame.jpeg", params={"src": src}, timeout=10
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("go2rtc frame grab failed: %s", exc)
        raise CaptureError("Could not grab a frame from the streaming relay.") from exc
    if not resp.content:
        raise CaptureError("go2rtc returned an empty frame — is anything publishing?")
    return resp.content


def relay_src(rtsp_url: str) -> str | None:
    from urllib.parse import urlparse

    from app.config import settings

    target = urlparse(settings.replay_target)
    url = urlparse(rtsp_url)
    if url.scheme == target.scheme and url.port == target.port:
        return url.path.rstrip("/").rsplit("/", 1)[-1] or None
    return None


def live_frame(rtsp_url: str) -> bytes:
    src = relay_src(rtsp_url)
    if src:
        return go2rtc_frame(src)
    return probe_snapshot(rtsp_url)


def snapshot_bytes(camera) -> bytes:
    if camera.role == "cctv":
        seg = capture_path(camera.id)
        if seg.exists():
            return file_snapshot(str(seg))
        return probe_snapshot(camera.rtsp_url)
    if not camera.rtsp_url.startswith(("rtsp://", "http://", "https://")):
        return file_snapshot(camera.rtsp_url)
    return go2rtc_frame(camera.rtsp_url.rstrip("/").rsplit("/", 1)[-1])


STOP_GRACE_S = 10.0


def request_stop(camera_id) -> bool:
    key = str(camera_id)
    with _active_lock:
        proc = _active.get(key)
        if proc is None or proc.poll() is not None:
            return False
        _stopped.add(key)
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.write(b"q")
            proc.stdin.flush()
        threading.Timer(STOP_GRACE_S, lambda: proc.poll() is None and proc.kill()).start()
        return True


def capture_segment(rtsp_url: str, dest: Path, duration_s: int, camera_id=None) -> None:
    duration_s = max(MIN_DURATION_S, min(int(duration_s), MAX_DURATION_S))
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part.mp4")
    key = str(camera_id) if camera_id is not None else None

    try:
        for attempt, vcodec in enumerate(
            (["-c:v", "copy"], ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"])
        ):
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-timeout",
                SOCKET_TIMEOUT_US,
                "-i",
                rtsp_url,
                "-t",
                str(duration_s),
                "-an",
                *vcodec,
                "-movflags",
                "+faststart",
                str(part),
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if key is not None:
                with _active_lock:
                    _stopped.discard(key)
                    _active[key] = proc
            try:
                proc.wait(timeout=duration_s + 60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise CaptureError("Capture timed out — the camera stalled.") from None
            finally:
                if key is not None:
                    with _active_lock:
                        _active.pop(key, None)
                with contextlib.suppress(OSError, ValueError):
                    proc.stdin.close()
            stderr_bytes = proc.stderr.read() if proc.stderr else b""
            with _active_lock:
                user_stopped = key is not None and key in _stopped
            if (proc.returncode == 0 or user_stopped) and _valid_segment(part):
                os.replace(part, dest)
                return
            if user_stopped:
                raise CaptureError(
                    "Capture stopped before enough footage was recorded (need at least ~3s)"
                )
            stderr = stderr_bytes.decode(errors="replace")[-300:]
            log.warning(
                "Capture attempt %d failed (rc=%s): %s",
                attempt + 1,
                proc.returncode,
                _scrub(stderr, rtsp_url),
            )
        raise CaptureError("Camera stream could not be recorded (both copy and re-encode failed)")
    finally:
        part.unlink(missing_ok=True)
        if key is not None:
            with _active_lock:
                _stopped.discard(key)


def _valid_segment(path: Path) -> bool:
    if not path.exists():
        return False
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return False
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        return fps > 0 and frames >= 3 * fps
    finally:
        cap.release()
