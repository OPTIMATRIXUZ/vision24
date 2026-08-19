import logging
import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
from ultralytics import YOLO

from app.config import settings

from worker.types import TrackedPerson

log = logging.getLogger(__name__)

BUNDLED_TRACKER = Path(__file__).parent / "botsort_reid.yaml"


class PersonDetector:
    def __init__(
        self,
        device: str = "mps",
        conf: float | None = None,
        model: str | None = None,
        imgsz: int | None = None,
        half: bool | None = None,
        tracker: str | None = None,
    ):
        self.model = YOLO(model or settings.yolo_model)
        self.conf = conf if conf is not None else settings.yolo_conf
        self.imgsz = imgsz if imgsz is not None else settings.yolo_imgsz
        self.tracker = tracker or settings.yolo_tracker or str(BUNDLED_TRACKER)
        self.device = device
        try:
            import torch

            if device == "mps" and not torch.backends.mps.is_available():
                log.warning("MPS not available, falling back to CPU")
                self.device = "cpu"
        except (ImportError, AttributeError, RuntimeError):
            self.device = "cpu"

        wants_half = half if half is not None else settings.yolo_half
        self.half = wants_half and self.device != "cpu"
        self._precision = {"quantize": 16} if self.half else {}

    def track(self, frame) -> list[TrackedPerson]:
        results = self.model.track(
            frame,
            persist=True,
            tracker=self.tracker,
            classes=[0],
            conf=self.conf,
            imgsz=self.imgsz,
            verbose=False,
            device=self.device,
            **self._precision,
        )
        return _to_tracks(results[0].boxes)

    def track_stream(
        self, path: str, stride: int
    ) -> Iterator[tuple[int, object, list[TrackedPerson]]]:
        results = self.model.track(
            source=path,
            stream=True,
            vid_stride=stride,
            persist=False,
            tracker=self.tracker,
            classes=[0],
            conf=self.conf,
            imgsz=self.imgsz,
            verbose=False,
            device=self.device,
            **self._precision,
        )
        for i, r in enumerate(results):
            yield (i + 1) * stride - 1, r.orig_img, _to_tracks(r.boxes)

    def track_stream_gated(
        self, path: str, stride: int, gate
    ) -> Iterator[tuple[int, object, list[TrackedPerson]]]:
        import queue as queue_mod
        import threading

        prefetch: queue_mod.Queue = queue_mod.Queue(maxsize=8)
        stop = threading.Event()
        error: list[BaseException] = []

        def produce() -> None:
            cap = cv2.VideoCapture(path)
            try:
                if not cap.isOpened():
                    error.append(RuntimeError(f"Cannot open video: {path}"))
                    return
                idx = -1
                while not stop.is_set():
                    if not cap.grab():
                        return
                    idx += 1
                    if (idx + 1) % stride != 0:
                        continue
                    ok, frame = cap.retrieve()
                    if not ok or frame is None:
                        continue
                    moving = gate is None or gate.motion(frame)
                    while not stop.is_set():
                        try:
                            prefetch.put((idx, frame, moving), timeout=0.5)
                            break
                        except queue_mod.Full:
                            continue
            finally:
                cap.release()
                while not stop.is_set():
                    try:
                        prefetch.put(None, timeout=0.5)
                        break
                    except queue_mod.Full:
                        continue

        producer = threading.Thread(target=produce, daemon=True, name="decode-prefetch")
        producer.start()
        active = False
        skipped = 0
        try:
            while True:
                item = prefetch.get()
                if item is None:
                    break
                idx, frame, moving = item
                if moving or active:
                    tracks = self.track(frame)
                    active = len(tracks) > 0
                else:
                    tracks = []
                    skipped += 1
                yield idx, frame, tracks
            if error:
                raise error[0]
        finally:
            stop.set()
            while True:
                try:
                    prefetch.get_nowait()
                except queue_mod.Empty:
                    break
            producer.join(timeout=2.0)
            if skipped:
                log.info("motion gate: skipped YOLO on %d static frames", skipped)


def _to_tracks(boxes) -> list[TrackedPerson]:
    if boxes is None or boxes.id is None:
        return []
    return [
        TrackedPerson(track_id=int(tid), xyxy=tuple(xyxy), conf=float(conf))
        for tid, xyxy, conf in zip(
            boxes.id.int().tolist(), boxes.xyxy.tolist(), boxes.conf.tolist(), strict=True
        )
    ]
