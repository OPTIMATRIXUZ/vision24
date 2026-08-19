import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time as time_mod
import uuid
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Camera
from app.services import worker_status
from worker.camera_spec import CameraSpec

log = logging.getLogger("worker.supervisor")

RESCAN_S = 15.0
RESTART_BACKOFF_S = 3.0
MAX_RESTART_BACKOFF_S = 60.0
HEALTHY_RUN_S = 120.0
TERM_GRACE_S = 15.0
BACKEND_DIR = Path(__file__).resolve().parents[1]
EXIT_GONE = 4
EXIT_LOCKED = 5


def live_cameras() -> list[CameraSpec]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(Camera).where(Camera.is_active, Camera.role == "cctv").order_by(Camera.name)
        ).all()
        return [CameraSpec(id=c.id, name=c.name, source=c.rtsp_url) for c in rows]


def dev_override_cameras() -> list[CameraSpec] | None:
    if not settings.video_source:
        return None
    with SessionLocal() as db:
        cams = db.scalars(select(Camera).where(Camera.is_active).order_by(Camera.name)).all()
    cam = next((c for c in cams if c.role == "cctv"), None) or (cams[0] if cams else None)
    if cam is None:
        return []
    return [CameraSpec(id=cam.id, name=cam.name, source=settings.video_source)]


class _Runner:

    def __init__(self, spec: CameraSpec):
        self.spec = spec
        self.proc: subprocess.Popen | None = None
        self.state = "starting"
        self.restarts = 0
        self.last_error: str | None = None
        self.backoff = RESTART_BACKOFF_S
        self.start_at = 0.0
        self.started_mono = 0.0

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class Supervisor:
    def __init__(self, show: bool = False, only: str | None = None):
        self.show = show
        self.only = only
        self._stop = threading.Event()
        self._runners: dict[uuid.UUID, _Runner] = {}


    def shutdown(self, *_args) -> None:
        if not self._stop.is_set():
            log.info("Shutting down — stopping %d camera process(es)", len(self._runners))
        self._stop.set()

    def run(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.shutdown)

        log.info("Supervisor up (pid=%s), rescanning cameras every %.0fs", os.getpid(), RESCAN_S)
        last_scan: float | None = None
        announced_idle = False
        try:
            while not self._stop.is_set():
                now = time_mod.monotonic()
                if last_scan is None or now - last_scan >= RESCAN_S:
                    try:
                        self._reconcile()
                    except Exception:
                        log.exception("Camera rescan failed — keeping current workers")
                    last_scan = now
                    if not self._runners and not announced_idle:
                        announced_idle = True
                        log.info(
                            "No active live cameras. Add a CCTV source in the UI (or set "
                            "VIDEO_SOURCE for the dev replay feed) — detection starts by itself."
                        )
                    elif self._runners:
                        announced_idle = False

                self._reap()
                self._start_due()
                self._publish()
                self._stop.wait(worker_status.HEARTBEAT_S)
        finally:
            self._teardown()

    def _teardown(self) -> None:
        for runner in list(self._runners.values()):
            self._terminate(runner, wait=False)
        deadline = time_mod.monotonic() + TERM_GRACE_S
        for runner in list(self._runners.values()):
            self._await_exit(runner, deadline)
        self._runners.clear()
        worker_status.clear()
        log.info("Supervisor stopped")


    def wanted(self) -> dict[uuid.UUID, CameraSpec]:
        specs = dev_override_cameras()
        if specs is None:
            specs = live_cameras()
        if self.only:
            needle = self.only.lower()
            specs = [s for s in specs if needle in s.name.lower() or str(s.id) == needle]
        return {s.id: s for s in specs}

    def _reconcile(self) -> None:
        wanted = self.wanted()

        for cam_id, runner in list(self._runners.items()):
            spec = wanted.get(cam_id)
            if spec is None:
                self._retire(runner, "camera removed or deactivated")
            elif spec.source != runner.spec.source:
                self._retire(runner, "stream URL changed")

        for cam_id, spec in wanted.items():
            if cam_id not in self._runners:
                runner = _Runner(spec)
                self._runners[cam_id] = runner
                self._spawn(runner)

    def _retire(self, runner: _Runner, reason: str) -> None:
        log.info("Stopping detection for camera '%s' (%s)", runner.spec.name, reason)
        self._terminate(runner)
        self._runners.pop(runner.spec.id, None)
        worker_status.clear_camera_stats(runner.spec.id)


    def _spawn(self, runner: _Runner) -> None:
        cmd = [sys.executable, "-m", "worker.camera_main", "--camera-id", str(runner.spec.id)]
        if self.show:
            cmd.append("--show")
        try:
            runner.proc = subprocess.Popen(cmd, cwd=str(BACKEND_DIR))
        except Exception as exc:
            runner.last_error = f"{type(exc).__name__}: {exc}"
            runner.state = "restarting"
            runner.start_at = time_mod.monotonic() + runner.backoff
            log.exception("Could not start detection for camera '%s'", runner.spec.name)
            return
        runner.state = "starting"
        runner.started_mono = time_mod.monotonic()
        runner.start_at = 0.0
        log.info("Started detection for camera '%s' (pid=%s)", runner.spec.name, runner.proc.pid)

    def _reap(self) -> None:
        for runner in list(self._runners.values()):
            if runner.proc is None or runner.alive:
                continue
            code = runner.proc.returncode
            runner.proc = None
            worker_status.clear_camera_stats(runner.spec.id)

            if code == EXIT_GONE:
                log.info("Camera '%s' no longer exists — not restarting", runner.spec.name)
                self._runners.pop(runner.spec.id, None)
                continue

            ran_for = time_mod.monotonic() - runner.started_mono
            if ran_for >= HEALTHY_RUN_S:
                runner.backoff = RESTART_BACKOFF_S
            runner.restarts += 1
            runner.state = "restarting"
            runner.start_at = time_mod.monotonic() + runner.backoff
            if code == EXIT_LOCKED:
                runner.last_error = "another process is detecting on this camera"
                log.warning(
                    "Camera '%s' is held by another detection process (orphan?) — "
                    "retrying in %.0fs",
                    runner.spec.name,
                    runner.backoff,
                )
            else:
                runner.last_error = None if code == 0 else f"exited with code {code}"
                log.warning(
                    "Camera '%s' exited (code %s) after %.0fs — restarting in %.0fs (restart #%d)",
                    runner.spec.name,
                    code,
                    ran_for,
                    runner.backoff,
                    runner.restarts,
                )
            runner.backoff = min(runner.backoff * 2, MAX_RESTART_BACKOFF_S)

    def _start_due(self) -> None:
        now = time_mod.monotonic()
        for runner in self._runners.values():
            if runner.proc is None and now >= runner.start_at:
                self._spawn(runner)

    def _terminate(self, runner: _Runner, wait: bool = True) -> None:
        if runner.proc is None or runner.proc.poll() is not None:
            return
        runner.proc.terminate()
        if wait:
            self._await_exit(runner, time_mod.monotonic() + TERM_GRACE_S)

    def _await_exit(self, runner: _Runner, deadline: float) -> None:
        if runner.proc is None:
            return
        try:
            runner.proc.wait(timeout=max(0.1, deadline - time_mod.monotonic()))
        except subprocess.TimeoutExpired:
            log.warning("Camera '%s' did not stop in time — killing", runner.spec.name)
            runner.proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                runner.proc.wait(timeout=5)
        runner.state = "stopped"


    def _publish(self) -> None:
        cameras = []
        for runner in self._runners.values():
            stats = worker_status.read_camera_stats(runner.spec.id) if runner.alive else None
            state = stats["state"] if stats else ("starting" if runner.alive else runner.state)
            if state == "running":
                runner.last_error = None
            cameras.append(
                {
                    "camera_id": str(runner.spec.id),
                    "name": runner.spec.name,
                    "pid": runner.proc.pid if runner.proc else None,
                    "state": state,
                    "fps": (stats or {}).get("fps", 0.0),
                    "tracks": (stats or {}).get("tracks", 0),
                    "events": (stats or {}).get("events", 0),
                    "started_at": (stats or {}).get("started_at"),
                    "last_event_at": (stats or {}).get("last_event_at"),
                    "restarts": runner.restarts,
                    "error": runner.last_error,
                }
            )
        try:
            worker_status.write(cameras)
        except Exception:
            log.exception("Could not publish worker heartbeat")
