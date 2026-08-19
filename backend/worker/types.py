from dataclasses import dataclass


@dataclass
class TrackedPerson:
    track_id: int
    xyxy: tuple[float, float, float, float]
    conf: float

    def foot_point(self, frame_w: int, frame_h: int) -> tuple[float, float]:
        x1, _y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2 / frame_w, y2 / frame_h)


@dataclass
class DetectedObject:

    xyxy: tuple[float, float, float, float]
    conf: float
    class_name: str
