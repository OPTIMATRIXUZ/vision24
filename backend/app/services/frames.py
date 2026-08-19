import logging
import os
import tempfile
from collections.abc import Iterable, Iterator

import cv2
import numpy as np

log = logging.getLogger(__name__)

MAX_WIDTH = 640
JPEG_QUALITY = 70
PIXELATE_BLOCK = 24


def read_frames_at(path, indices: Iterable[int]) -> Iterator[tuple[int, "np.ndarray"]]:
    wanted = sorted({int(i) for i in indices if i >= 0})
    if not wanted:
        return
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {path}")
        pos = -1
        for idx in wanted:
            while pos < idx:
                if not cap.grab():
                    return
                pos += 1
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                yield idx, frame
    finally:
        cap.release()


def apply_privacy_masks(frame, zones) -> "np.ndarray":
    masked = [z for z in zones if getattr(z, "privacy_mask", False)]
    if not masked:
        return frame
    h, w = frame.shape[:2]
    region = np.zeros((h, w), dtype=np.uint8)
    for z in masked:
        try:
            pts = np.array([[int(x * w), int(y * h)] for x, y in z.polygon], np.int32)
            if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
                raise ValueError(f"polygon is not a list of >=3 points: shape {pts.shape}")
            cv2.fillPoly(region, [pts], 255)
        except Exception:
            log.exception(
                "Privacy zone %s has an unusable polygon — obscuring the entire "
                "frame rather than letting the unmasked region through.",
                getattr(z, "id", "?"),
            )
            region[:] = 255
            break
    if not region.any():
        return frame
    small = cv2.resize(
        frame,
        (max(1, w // PIXELATE_BLOCK), max(1, h // PIXELATE_BLOCK)),
        interpolation=cv2.INTER_LINEAR,
    )
    pixelated = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    frame[region == 255] = pixelated[region == 255]
    return frame


def mask_jpegs(jpegs: list[bytes], zones) -> list[bytes]:
    if not any(getattr(z, "privacy_mask", False) for z in zones):
        return jpegs
    out: list[bytes] = []
    for buf in jpegs:
        img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        apply_privacy_masks(img, zones)
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            out.append(enc.tobytes())
    return out


def prepare_vlm_jpeg(jpeg: bytes, zones) -> bytes | None:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    apply_privacy_masks(img, zones)
    return _encode(img)


def prepare_vlm_frame(frame_bgr, zones) -> bytes | None:
    apply_privacy_masks(frame_bgr, zones)
    return _encode(frame_bgr)


def _encode(frame) -> bytes | None:
    h, w = frame.shape[:2]
    if w > MAX_WIDTH:
        scale = MAX_WIDTH / w
        frame = cv2.resize(frame, (MAX_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buf.tobytes() if ok else None


def compress_presence(counts: list[int]) -> list[list[int]] | None:
    runs: list[list[int]] = []
    for i, n in enumerate(counts):
        if n <= 0:
            continue
        if runs and runs[-1][1] == i - 1:
            runs[-1][1] = i
            runs[-1][2] = max(runs[-1][2], n)
        else:
            runs.append([i, i, n])
    return runs or None


def _pick_frames(total: int, count: int, people_frames, anchor_frac) -> list[int]:
    if people_frames:
        occupied = sorted(
            {i for start, end, _ in people_frames for i in range(start, end + 1) if 0 <= i < total}
        )
        if occupied:
            if len(occupied) <= count:
                return occupied
            picks = [
                occupied[round(j * (len(occupied) - 1) / max(1, count - 1))] for j in range(count)
            ]
            busiest = max(people_frames, key=lambda r: r[2])
            mid = min(total - 1, (busiest[0] + busiest[1]) // 2)
            if mid not in picks:
                picks[min(range(count), key=lambda j: abs(picks[j] - mid))] = mid
            return sorted(set(picks))
    start = 0
    if anchor_frac is not None:
        start = min(max(int(anchor_frac * total), 0), total - 1)
    span = total - start
    return [start + int(span / (count + 1) * (i + 1)) for i in range(count)]


def sample_jpeg_frames(
    video_bytes: bytes,
    count: int = 3,
    people_frames: list | None = None,
    anchor_frac: float | None = None,
) -> list[bytes]:
    if count < 1 or not video_bytes:
        return []
    tmp_path = None
    cap = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frames: list[bytes] = []
        if total > 0:
            cap.release()
            cap = None
            wanted = _pick_frames(total, count, people_frames, anchor_frac)
            for _idx, frame in read_frames_at(tmp_path, wanted):
                jpg = _encode(frame)
                if jpg:
                    frames.append(jpg)
        else:
            spacing = 5
            read = 0
            while len(frames) < count:
                ok, frame = cap.read()
                if not ok:
                    break
                read += 1
                if read % spacing == 0:
                    jpg = _encode(frame)
                    if jpg:
                        frames.append(jpg)
        return frames
    except Exception:
        log.exception("frame sampling failed")
        return []
    finally:
        if cap is not None:
            cap.release()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
