import logging
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from ultralytics import YOLO

from app.config import settings
from worker.types import DetectedObject

log = logging.getLogger(__name__)


class PackageDetector:
    def __init__(
        self, device: str = "mps", conf: float | None = None, prompts: list[str] | None = None
    ):
        self.model = YOLO(settings.delivery_model)
        self.conf = conf if conf is not None else settings.delivery_conf
        self.device = device
        try:
            import torch

            if device == "mps" and not torch.backends.mps.is_available():
                log.warning("MPS not available, falling back to CPU")
                self.device = "cpu"
        except (ImportError, AttributeError, RuntimeError):
            self.device = "cpu"
        names = prompts or [p.strip() for p in settings.delivery_prompts.split(",") if p.strip()]
        self.model.set_classes(names)

    def detect(self, crop_bgr) -> list[DetectedObject]:
        results = self.model.predict(
            crop_bgr,
            conf=self.conf,
            iou=0.5,
            imgsz=640,
            agnostic_nms=True,
            max_det=8,
            verbose=False,
            device=self.device,
        )
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []
        names = results[0].names
        return [
            DetectedObject(xyxy=tuple(xyxy), conf=float(conf), class_name=names.get(int(cls), "?"))
            for xyxy, conf, cls in zip(
                boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), strict=True
            )
        ]

    def close(self) -> None:
        del self.model
        try:
            import torch

            if self.device == "mps":
                torch.mps.empty_cache()
        except Exception:  # noqa: BLE001 - best-effort cache release
            pass
