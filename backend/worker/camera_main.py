import argparse
import logging
import os
import signal
import threading

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

from worker import procguard  # noqa: F401

import uuid


from app.config import settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.models import Camera
from app.services import locks, worker_status
from worker.camera_spec import CameraSpec
from worker.camera_worker import CameraWorker

configure_logging(
    "worker-camera",
    level=settings.log_level,
    fmt=settings.log_format,
    log_dir=settings.log_path,
)
log = logging.getLogger("worker.camera_main")

EXIT_GONE = 4
EXIT_LOCKED = 5


def acquire_camera_lock(camera_id: uuid.UUID) -> bool:
    return locks.try_acquire(locks.camera_lock_name(camera_id))


def load_spec(camera_id: uuid.UUID) -> CameraSpec | None:
    with SessionLocal() as db:
        cam = db.get(Camera, camera_id)
        if cam is None or not cam.is_active:
            return None
        return CameraSpec(id=cam.id, name=cam.name, source=settings.video_source or cam.rtsp_url)


def publish_stats(worker: CameraWorker, stop: threading.Event) -> None:
    while not stop.wait(worker_status.HEARTBEAT_S):
        try:
            worker_status.write_camera_stats(
                worker.spec.id,
                {
                    "state": worker.state,
                    "fps": round(worker.observed_fps, 1),
                    "tracks": worker.track_count,
                    "events": worker.event_count,
                    "started_at": _iso(worker.started_at),
                    "last_event_at": _iso(worker.last_event_at),
                },
            )
        except Exception:
            log.exception("Could not publish camera stats")


def main() -> int:
    parser = argparse.ArgumentParser(description="Vision 24 single-camera detection loop")
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--show", action="store_true", help="draw boxes in a debug window")
    args = parser.parse_args()

    camera_id = uuid.UUID(args.camera_id)
    spec = load_spec(camera_id)
    if spec is None:
        log.warning("Camera %s is gone or inactive — exiting", args.camera_id)
        return EXIT_GONE

    if not acquire_camera_lock(camera_id):
        log.warning("Camera '%s' is already being detected by another process", spec.name)
        return EXIT_LOCKED

    worker = CameraWorker(spec, show=args.show)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: worker.stop())

    stop_stats = threading.Event()
    stats_thread = threading.Thread(
        target=publish_stats, args=(worker, stop_stats), daemon=True, name="stats"
    )
    stats_thread.start()
    try:
        worker.run()
    finally:
        stop_stats.set()
        worker_status.clear_camera_stats(spec.id)
    return 0


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
