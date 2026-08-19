import cv2
import numpy as np


class MotionGate:
    def __init__(self, min_ratio: float = 0.003, pixel_delta: int = 25, width: int = 320):
        self.min_ratio = min_ratio
        self.pixel_delta = pixel_delta
        self.width = width
        self._prev = None

    def _prep(self, frame):
        h, w = frame.shape[:2]
        if w > self.width:
            scale = self.width / w
            frame = cv2.resize(
                frame, (self.width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA
            )
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def motion(self, frame) -> bool:
        gray = self._prep(frame)
        prev = self._prev
        self._prev = gray
        if prev is None or prev.shape != gray.shape:
            return True
        diff = cv2.absdiff(gray, prev)
        changed = int(np.count_nonzero(diff > self.pixel_delta))
        return (changed / diff.size) >= self.min_ratio
