import cv2
import numpy as np
import supervision as sv

from worker.types import TrackedPerson

BOX_THICKNESS = 2
ZONE_FILL_ALPHA = 0.18
TRACE_LENGTH = 30
HEATMAP_ALPHA = 0.6

ZONE_COLORS = {
    "entrance": (80, 200, 20),
    "checkout_area": (0, 150, 255),
    "store_room": (60, 60, 230),
    "dining": (230, 130, 40),
    "truck": (0, 190, 240),
    "delivery_door": (200, 80, 160),
    "custom": (150, 150, 150),
}


def to_sv_detections(tracks: list[TrackedPerson]) -> sv.Detections:
    if not tracks:
        return sv.Detections.empty()
    return sv.Detections(
        xyxy=np.array([t.xyxy for t in tracks], dtype=np.float32),
        confidence=np.array([t.conf for t in tracks], dtype=np.float32),
        tracker_id=np.array([t.track_id for t in tracks], dtype=int),
    )


class TrackAnnotator:

    def __init__(self, trace: bool = True):
        lookup = sv.ColorLookup.TRACK
        self._box = sv.BoxAnnotator(thickness=BOX_THICKNESS, color_lookup=lookup)
        self._label = sv.LabelAnnotator(text_scale=0.5, color_lookup=lookup)
        self._trace = (
            sv.TraceAnnotator(
                trace_length=TRACE_LENGTH,
                thickness=BOX_THICKNESS,
                position=sv.Position.BOTTOM_CENTER,
                color_lookup=lookup,
            )
            if trace
            else None
        )

    def draw(self, frame, tracks: list[TrackedPerson]):
        det = to_sv_detections(tracks)
        if len(det) == 0:
            return frame
        if self._trace is not None:
            frame = self._trace.annotate(frame, det)
        frame = self._box.annotate(frame, det)
        return self._label.annotate(frame, det, labels=[f"#{t.track_id}" for t in tracks])


def draw_hud(frame, lines: list[str]):
    if not lines:
        return frame
    pad, line_h, scale = 10, 26, 0.6
    width = (
        max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0][0] for t in lines) + 2 * pad
    )
    height = line_h * len(lines) + pad
    height = min(height, frame.shape[0])
    width = min(width, frame.shape[1])
    roi = frame[0:height, 0:width]
    dark = np.zeros_like(roi)
    cv2.addWeighted(dark, 0.55, roi, 0.45, 0, roi)
    y = line_h - 4
    for t in lines:
        cv2.putText(frame, t, (pad, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2)
        y += line_h
    return frame


def draw_zones(frame, zones):
    if not zones:
        return frame
    h, w = frame.shape[:2]
    polys = []
    for z in zones:
        try:
            pts = np.array([[int(x * w), int(y * h)] for x, y in z.polygon], np.int32)
        except (TypeError, ValueError):
            continue
        polys.append((pts, ZONE_COLORS.get(z.kind, ZONE_COLORS["custom"]), z.name))
    if not polys:
        return frame

    overlay = frame.copy()
    for pts, color, _ in polys:
        cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, ZONE_FILL_ALPHA, frame, 1 - ZONE_FILL_ALPHA, 0, frame)
    for pts, color, name in polys:
        cv2.polylines(frame, [pts], True, color, 2)
        lx, ly = pts[pts[:, 1].argmin()]
        cv2.putText(
            frame, name, (int(lx), max(int(ly) - 6, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2
        )
    return frame


_UNICODE_FONT_PATHS = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def _unicode_font(size: int):
    import os

    from PIL import ImageFont

    for path in _UNICODE_FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def _draw_texts(frame, texts: list[tuple[int, int, str]], size: int = 14):
    font = _unicode_font(size)
    if font is None:
        for x, y, text in texts:
            safe = text.encode("ascii", "replace").decode()
            cv2.putText(
                frame, safe, (x, y + size), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1
            )
        return frame
    from PIL import Image, ImageDraw

    image = Image.fromarray(frame[:, :, ::-1])
    draw = ImageDraw.Draw(image)
    for x, y, text in texts:
        draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=(255, 255, 255))
    frame[:] = np.asarray(image)[:, :, ::-1]
    return frame


def build_trip_montage(
    panels: list[tuple[np.ndarray, list[tuple[tuple, int, str]]]], hud_text: str
) -> np.ndarray | None:
    if not panels:
        return None
    box = sv.BoxAnnotator(thickness=BOX_THICKNESS, color_lookup=sv.ColorLookup.CLASS)
    height = max(frame.shape[0] for frame, _ in panels)
    rendered = []
    labels: list[tuple[int, int, str]] = []
    x_offset = 0
    for frame, dets in panels:
        frame = frame.copy()
        if dets:
            detections = sv.Detections(
                xyxy=np.array([d[0] for d in dets], dtype=np.float32),
                class_id=np.array([d[1] for d in dets], dtype=int),
            )
            frame = box.annotate(frame, detections)
        if frame.shape[0] != height:
            scale = height / frame.shape[0]
            frame = cv2.resize(frame, (max(1, int(frame.shape[1] * scale)), height))
        else:
            scale = 1.0
        for xyxy, _cid, label in dets:
            lx = x_offset + int(xyxy[0] * scale)
            ly = max(22, int(xyxy[1] * scale) - 18)
            labels.append((lx, ly, label))
        rendered.append(frame)
        rendered.append(np.full((height, 4, 3), 255, dtype=np.uint8))
        x_offset += frame.shape[1] + 4
    montage = np.hstack(rendered[:-1])
    banner_h = 26
    roi = montage[0:banner_h, 0 : montage.shape[1]]
    cv2.addWeighted(np.zeros_like(roi), 0.55, roi, 0.45, 0, roi)
    return _draw_texts(montage, [(8, 4, hud_text), *labels], size=15)


def render_heatmap(heat: np.ndarray, background) -> bytes | None:
    if heat is None or background is None or not heat.any():
        return None
    h, w = background.shape[:2]
    blurred = cv2.GaussianBlur(heat, (0, 0), sigmaX=2.0)
    norm = np.sqrt(blurred / blurred.max())
    up = cv2.resize(norm, (w, h), interpolation=cv2.INTER_CUBIC).clip(0.0, 1.0)
    color = cv2.applyColorMap((up * 255).astype(np.uint8), cv2.COLORMAP_JET)
    alpha = (up * HEATMAP_ALPHA)[..., None]
    out = background.astype(np.float32) * (1 - alpha) + color.astype(np.float32) * alpha
    ok, jpeg = cv2.imencode(".jpg", out.astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return jpeg.tobytes() if ok else None
