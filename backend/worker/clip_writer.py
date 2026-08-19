import logging
import queue
import subprocess
import tempfile
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta

import cv2

from app import storage
from app.db import SessionLocal
from app.models import Clip
from app.services.frames import compress_presence

log = logging.getLogger(__name__)

PRE_S = 8.0
POST_S = 4.0
JPEG_QUALITY = 80
ENCODE_WAIT_TIMEOUT_S = 15.0


class ClipWriter:
    def __init__(self, fps: int):
        self.fps = fps
        self.buffer: deque[tuple[datetime, bytes, int]] = deque(
            maxlen=int((PRE_S + POST_S + 2) * fps)
        )
        self.jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="clip-writer")
        self._thread.start()

    def add_frame(self, ts: datetime, frame, people: int = 0) -> None:
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            self.buffer.append((ts, jpeg.tobytes(), people))

    def request_snapshot(self, event_id: int, trigger_ts: datetime) -> None:
        self.jobs.put({"kind": "snapshot", "event_id": event_id, "ts": trigger_ts})

    def request_clip(self, event_id: int, trigger_ts: datetime) -> None:
        self.jobs.put({"kind": "clip", "event_id": event_id, "ts": trigger_ts})


    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                if job["kind"] == "snapshot":
                    self._do_snapshot(job["event_id"], job["ts"])
                else:
                    self._do_clip(job["event_id"], job["ts"])
            except Exception:
                log.exception("Clip job failed: %s", job)

    def _closest_frame(self, ts: datetime) -> bytes | None:
        frames = list(self.buffer)
        if not frames:
            return None
        return min(frames, key=lambda item: abs((item[0] - ts).total_seconds()))[1]

    def _upsert_clip_row(self, event_id: int, ts: datetime, **fields) -> None:
        with SessionLocal() as db:
            clip = db.query(Clip).filter(Clip.event_id == event_id).first()
            if clip is None:
                clip = Clip(event_id=event_id, ts_start=ts)
                db.add(clip)
            for key, value in fields.items():
                setattr(clip, key, value)
            db.commit()

    def _do_snapshot(self, event_id: int, ts: datetime) -> None:
        jpeg = self._closest_frame(ts)
        if jpeg is None:
            return
        key = f"snapshots/{event_id}-{uuid.uuid4().hex[:8]}.jpg"
        storage.upload_bytes(key, jpeg, "image/jpeg")
        self._upsert_clip_row(event_id, ts, snapshot_key=key)

    def _do_clip(self, event_id: int, trigger_ts: datetime) -> None:
        deadline = trigger_ts + timedelta(seconds=POST_S)
        waited = 0.0
        while waited < ENCODE_WAIT_TIMEOUT_S:
            if self.buffer and self.buffer[-1][0] >= deadline:
                break
            threading.Event().wait(0.25)
            waited += 0.25

        window_start = trigger_ts - timedelta(seconds=PRE_S)
        window = [(f, p) for (ts, f, p) in list(self.buffer) if window_start <= ts <= deadline]
        if len(window) < self.fps:
            log.warning("Not enough buffered frames for event %s clip", event_id)
            return

        mp4 = self._encode([f for f, _ in window])
        if mp4 is None:
            return
        key = f"clips/{event_id}-{uuid.uuid4().hex[:8]}.mp4"
        storage.upload_bytes(key, mp4, "video/mp4")
        self._upsert_clip_row(
            event_id,
            window_start,
            storage_key=key,
            duration_s=round(len(window) / self.fps, 1),
            people_frames=compress_presence([p for _, p in window]),
        )
        log.info("Stored clip %s (%d frames) for event %s", key, len(window), event_id)

    def _encode(self, jpeg_frames: list[bytes]) -> bytes | None:
        with tempfile.NamedTemporaryFile(suffix=".mp4") as out:
            proc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "-framerate",
                    str(self.fps),
                    "-i",
                    "-",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    out.name,
                ],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                for jpeg in jpeg_frames:
                    proc.stdin.write(jpeg)
                proc.stdin.close()
                proc.wait(timeout=60)
            except Exception:
                proc.kill()
                log.exception("ffmpeg encode failed")
                return None
            if proc.returncode != 0:
                log.error("ffmpeg exited %s: %s", proc.returncode, proc.stderr.read()[-500:])
                return None
            out.seek(0)
            return out.read()
