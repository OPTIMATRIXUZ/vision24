import contextlib
import logging
import os
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
from sqlalchemy import delete, select, text

from app import storage
from app.config import settings
from app.db import SessionLocal
from app.models import Alert, AlertRule, Clip, Embedding, Event, Zone
from app.services import telegram
from app.services.frames import apply_privacy_masks, compress_presence, read_frames_at
from worker.alert_engine import AlertEngine
from worker.annotate import TrackAnnotator, draw_hud, draw_zones, render_heatmap
from worker.delivery import Trip, TripSegmenter, run_delivery_pipeline
from worker.detector import PersonDetector, TrackedPerson
from worker.motion import MotionGate
from worker.zone_engine import ZoneEngine

log = logging.getLogger(__name__)

MAX_CLIPS = 20
MAX_SNAPSHOTS = 100
CLIP_PRE_S = 8.0
CLIP_POST_S = 4.0
JPEG_QUALITY = 85
HEATMAP_GRID_W = 160

_upsert_lock = threading.Lock()
_snapshot_annotator = TrackAnnotator(trace=False)


class _StreamEncoder:

    def __init__(self, fps: float):
        self.out = tempfile.NamedTemporaryFile(suffix=".mp4")  # noqa: SIM115
        self.proc = subprocess.Popen(
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
                f"{fps:.3f}",
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                self.out.name,
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.frames = 0
        self.failed = False

    def write(self, frame) -> None:
        if self.failed:
            return
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return
        try:
            self.proc.stdin.write(jpeg.tobytes())
            self.frames += 1
        except Exception:
            log.exception("Processed-video encoder pipe broke; continuing without it")
            self.failed = True
            self.proc.kill()

    def finish(self) -> bytes | None:
        if self.failed or self.frames == 0:
            self.out.close()
            return None
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=300)
        except Exception:
            log.exception("Processed-video encode failed")
            self.proc.kill()
            self.out.close()
            return None
        if self.proc.returncode != 0:
            log.error(
                "Processed-video ffmpeg exited %s: %s",
                self.proc.returncode,
                self.proc.stderr.read()[-300:],
            )
            self.out.close()
            return None
        self.out.seek(0)
        data = self.out.read()
        self.out.close()
        return data


def _clear_previous_results(db, camera_id) -> None:
    event_ids = select(Event.id).where(Event.camera_id == camera_id)
    db.execute(delete(Alert).where(Alert.event_id.in_(event_ids)))
    db.execute(delete(Embedding).where(Embedding.event_id.in_(event_ids)))
    db.execute(delete(Clip).where(Clip.event_id.in_(event_ids)))
    db.execute(delete(Event).where(Event.camera_id == camera_id))
    db.commit()


def _processed_floor(idx: int, stride: int) -> int:
    return max(stride - 1, idx - (idx - (stride - 1)) % stride)


def _persist_events(
    camera_id,
    specs,
    frame_idx: int,
    alert_engine: AlertEngine,
    snapshot_jobs: list,
    clip_jobs: list,
) -> tuple[int, list]:
    entry_zone_ids = []
    with SessionLocal() as db:
        for spec in specs:
            row = Event(
                camera_id=camera_id,
                zone_id=spec.zone_id,
                type=spec.type,
                track_id=spec.track_id,
                ts_start=spec.ts_start,
                ts_end=spec.ts_end,
                attributes=spec.attributes,
            )
            db.add(row)
            db.flush()
            if spec.type == "entry":
                entry_zone_ids.append(spec.zone_id)
                if len(snapshot_jobs) < MAX_SNAPSHOTS:
                    snapshot_jobs.append((row.id, frame_idx, spec.track_id))
                if spec.record_clip and len(clip_jobs) < MAX_CLIPS:
                    clip_jobs.append((row.id, frame_idx))
            for alert in alert_engine.process(spec):
                db.add(
                    Alert(
                        rule_id=alert.rule_id,
                        event_id=row.id,
                        triggered_at=alert.triggered_at,
                        value=alert.value,
                        message=alert.message,
                    )
                )
                if len(clip_jobs) < MAX_CLIPS:
                    clip_jobs.append((row.id, frame_idx))
                if len(snapshot_jobs) < MAX_SNAPSHOTS:
                    snapshot_jobs.append((row.id, frame_idx, spec.track_id))
                telegram.send_alert(
                    f"⚠️ Vision24: {alert.message}", camera_id=camera_id, event_id=row.id
                )
        db.commit()
    return len(specs), entry_zone_ids


def _describe_checkout_visits(
    camera_id,
    path: str,
    zones,
    anchor: datetime,
    end_ts: datetime,
    native_fps: float,
    total_frames: int,
    snapshot_jobs: list,
) -> int:
    from app.services import purchase_vision
    from app.services.ai.provider import is_configured
    from app.services.pos import _zone_presence

    if not settings.pos_vlm_verify or not is_configured():
        return 0
    checkout_zones = [z for z in zones if z.kind == "checkout_area"]
    if not checkout_zones:
        return 0

    def to_idx(ts: datetime) -> int:
        return min(total_frames - 1, max(0, round((ts - anchor).total_seconds() * native_fps)))

    try:
        windows: list[purchase_vision.VisitWindow] = []
        spans: list[tuple] = []
        with SessionLocal() as db:
            for zone in checkout_zones:
                for iv in _zone_presence(db, zone, anchor, end_ts).intervals:
                    windows.append(
                        purchase_vision.VisitWindow(
                            zone_id=zone.id,
                            polygon=zone.polygon,
                            start_idx=to_idx(iv.start),
                            end_idx=to_idx(iv.end),
                        )
                    )
                    spans.append((zone, iv))
        if not windows:
            return 0

        site_id = checkout_zones[0].site_id
        catalog_names, references = purchase_vision.load_checkout_catalog(site_id)

        verdicts = purchase_vision.describe_visits(
            path, windows, zones, catalog_names=catalog_names, references=references
        )

        written = 0
        with SessionLocal() as db:
            for (zone, iv), window, verdict in zip(spans, windows, verdicts, strict=True):
                if verdict is None:
                    continue
                row = Event(
                    camera_id=camera_id,
                    zone_id=zone.id,
                    type="checkout_visit",
                    track_id=None,
                    ts_start=iv.start,
                    ts_end=iv.end,
                    attributes={
                        "kind": verdict.kind,
                        "items": verdict.items,
                        "confidence": verdict.confidence,
                        "notes": verdict.notes,
                    },
                )
                db.add(row)
                db.flush()
                if len(snapshot_jobs) < MAX_SNAPSHOTS:
                    snapshot_jobs.append((row.id, (window.start_idx + window.end_idx) // 2, None))
                written += 1
            db.commit()
        log.info("Checkout visits described: %d of %d", written, len(windows))
        return written
    except Exception:
        log.exception("Checkout visit description failed — analysis continues")
        return 0


def _write_peak_occupancy(camera_id, peak_cam: tuple, peak_zones: dict) -> None:
    rows = []
    if peak_cam[0] > 0 and peak_cam[1] is not None:
        rows.append(
            Event(
                camera_id=camera_id,
                zone_id=None,
                type="occupancy",
                ts_start=peak_cam[1],
                attributes={"count": peak_cam[0], "peak": True},
            )
        )
    for zone_id, (count, ts) in peak_zones.items():
        if count > 0 and ts is not None:
            rows.append(
                Event(
                    camera_id=camera_id,
                    zone_id=zone_id,
                    type="occupancy",
                    ts_start=ts,
                    attributes={"count": count, "peak": True},
                )
            )
    if rows:
        with SessionLocal() as db:
            db.add_all(rows)
            db.commit()


def analyze_video(
    camera_id, path: str, progress_cb=None, anchor_end: datetime | None = None
) -> dict:
    detector = PersonDetector(device=settings.yolo_device)
    engine = ZoneEngine(min_track_age_s=settings.min_track_age_s)
    alert_engine = AlertEngine()

    with SessionLocal() as db:
        zones = db.scalars(select(Zone).where(Zone.camera_id == camera_id)).all()
        engine.update_zones(zones)
        segmenter = None
        if settings.delivery_enabled:
            truck_zones = [z for z in zones if z.kind == "truck"]
            door_zones = [z for z in zones if z.kind == "delivery_door"]
            if truck_zones and door_zones:
                segmenter = TripSegmenter(
                    truck_zones, door_zones, min_track_age_s=settings.min_track_age_s
                )
        rules = db.execute(
            select(AlertRule, Zone.name)
            .join(Zone, AlertRule.zone_id == Zone.id)
            .where(AlertRule.is_active, Zone.camera_id == camera_id)
        ).all()
        alert_engine.update_rules(rules)
        _clear_previous_results(db, camera_id)

    probe = cv2.VideoCapture(path)
    if not probe.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    native_fps = probe.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(probe.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    probe.release()
    duration_s = total_frames / native_fps
    stride = max(1, round(native_fps / settings.worker_fps))
    end_time = anchor_end or datetime.now(UTC)
    anchor = end_time - timedelta(seconds=duration_s)

    snapshot_jobs: list[tuple[int, int, int | None]] = []
    clip_jobs: list[tuple[int, int]] = []
    trips: list[Trip] = []
    pass1_share = 0.8 if segmenter is not None else 0.9
    detections: dict[int, list[TrackedPerson]] = {}
    events_written = 0
    entries_run = 0
    zone_entries: dict = {}
    peak_cam: tuple[int, datetime | None] = (0, None)
    peak_zones: dict = {}

    encoder = _StreamEncoder(fps=native_fps / stride)
    track_annotator = TrackAnnotator()
    heat: np.ndarray | None = None
    heat_background = None

    if settings.motion_gate_enabled:
        gate = MotionGate(
            min_ratio=settings.motion_min_ratio, pixel_delta=settings.motion_pixel_delta
        )
        stream = detector.track_stream_gated(path, stride, gate)
    else:
        stream = detector.track_stream(path, stride)

    for frame_idx, frame, tracks in stream:
        video_t = frame_idx / native_fps
        ts = anchor + timedelta(seconds=video_t)
        frame_h, frame_w = frame.shape[:2]
        if tracks:
            detections[frame_idx] = tracks
        events = engine.process(tracks, ts, frame_w, frame_h)
        if segmenter is not None:
            trips.extend(segmenter.observe(tracks, ts, frame_idx, frame_w, frame_h))
        if events:
            count, entry_zone_ids = _persist_events(
                camera_id, events, frame_idx, alert_engine, snapshot_jobs, clip_jobs
            )
            events_written += count
            for zone_id in entry_zone_ids:
                entries_run += 1
                if zone_id is not None:
                    zone_entries[zone_id] = zone_entries.get(zone_id, 0) + 1

        counts = engine.zone_counts()
        if engine.last_presence > peak_cam[0]:
            peak_cam = (engine.last_presence, ts)
        for zone_id, count in counts.items():
            if count > peak_zones.get(zone_id, (0, None))[0]:
                peak_zones[zone_id] = (count, ts)

        apply_privacy_masks(frame, zones)
        if heat is None:
            grid_h = max(1, round(HEATMAP_GRID_W * frame_h / frame_w))
            heat = np.zeros((grid_h, HEATMAP_GRID_W), dtype=np.float32)
            heat_background = frame.copy()
        for t in tracks:
            fx, fy = t.foot_point(frame_w, frame_h)
            gy = min(heat.shape[0] - 1, max(0, int(fy * heat.shape[0])))
            gx = min(heat.shape[1] - 1, max(0, int(fx * heat.shape[1])))
            heat[gy, gx] += 1.0

        draw_zones(frame, zones)
        frame = track_annotator.draw(frame, tracks)
        hud = [
            f"{int(video_t // 60):02d}:{int(video_t % 60):02d}"
            f"  people:{engine.last_presence}  entries:{entries_run}"
        ]
        hud.extend(
            f"{z.name}: {counts.get(z.id, 0)} in / {zone_entries.get(z.id, 0)} total"
            for z in zones[:4]
        )
        if segmenter is not None:
            hud.append(f"deliveries: {segmenter.completed_count} trips")
        draw_hud(frame, hud)
        encoder.write(frame)

        if progress_cb:
            progress_cb(min(pass1_share, frame_idx / total_frames * pass1_share), events_written)

    end_ts = anchor + timedelta(seconds=duration_s)
    flush_events = engine.flush(end_ts)
    if flush_events:
        last_idx = _processed_floor(total_frames - 1, stride)
        count, _ = _persist_events(
            camera_id, flush_events, last_idx, alert_engine, snapshot_jobs, clip_jobs
        )
        events_written += count

    _write_peak_occupancy(camera_id, peak_cam, peak_zones)

    if segmenter is not None:
        trips.extend(segmenter.flush())
        delivery_events = run_delivery_pipeline(
            camera_id,
            path,
            trips,
            zones,
            stride,
            segmenter.incomplete_count,
            segmenter.door_zone_id,
            end_ts,
        )
        if delivery_events:
            events_written += delivery_events
        if progress_cb:
            progress_cb(0.9, events_written)

    events_written += _describe_checkout_visits(
        camera_id, path, zones, anchor, end_ts, native_fps, total_frames, snapshot_jobs
    )

    processed = encoder.finish()
    if processed is not None:
        key = f"processed/{camera_id}.mp4"
        storage.upload_bytes(key, processed, "video/mp4")
        log.info(
            "Processed video uploaded: %s (%d frames, %.1f MB)",
            key,
            encoder.frames,
            len(processed) / 1e6,
        )

    heatmap_jpeg = render_heatmap(heat, heat_background)
    if heatmap_jpeg is not None:
        heatmap_key = f"heatmaps/{camera_id}.jpg"
        storage.upload_bytes(heatmap_key, heatmap_jpeg, "image/jpeg")
        log.info("Traffic heatmap uploaded: %s", heatmap_key)

    log.info(
        "Analysis pass done: %d events, %d clip jobs, %d snapshot jobs",
        events_written,
        len(clip_jobs),
        len(snapshot_jobs),
    )

    snapshot_jobs.extend(
        _metric_snapshot_jobs(
            camera_id,
            zones,
            anchor,
            native_fps,
            stride,
            total_frames,
            existing={eid for eid, _, _ in snapshot_jobs},
        )
    )

    done = 0
    total_jobs = len(clip_jobs) + len(snapshot_jobs) or 1
    crops: list[tuple[int, object]] = []
    crops_lock = threading.Lock()

    def bump() -> None:
        nonlocal done
        done += 1
        if progress_cb:
            progress_cb(0.9 + 0.1 * done / total_jobs, events_written)

    snaps_by_idx: dict[int, list[tuple[int, int, int | None]]] = {}
    for job in snapshot_jobs:
        snaps_by_idx.setdefault(job[1], []).append(job)

    pending_clips: list[tuple[int, int, int]] = []
    for event_id, trigger_idx in clip_jobs:
        start_idx = max(0, trigger_idx - int(CLIP_PRE_S * native_fps))
        end_idx = min(total_frames, trigger_idx + int(CLIP_POST_S * native_fps))
        if end_idx - start_idx < native_fps:
            bump()
            continue
        pending_clips.append((event_id, start_idx, end_idx))
    pending_clips.sort(key=lambda c: c[1])

    wanted: set[int] = set(snaps_by_idx)
    for _eid, start_idx, end_idx in pending_clips:
        wanted.update(range(start_idx, end_idx))

    active: list[_ClipWriter] = []
    queue = list(pending_clips)
    try:
        for idx, frame in read_frames_at(path, wanted):
            while queue and queue[0][1] <= idx:
                event_id, start_idx, end_idx = queue.pop(0)
                active.append(_ClipWriter(event_id, start_idx, end_idx, native_fps, zones))

            for job in snaps_by_idx.get(idx, ()):
                event_id, _trigger_idx, track_id = job
                try:
                    _render_snapshot(
                        frame.copy(),
                        event_id,
                        idx,
                        track_id,
                        anchor,
                        native_fps,
                        detections,
                        zones,
                        crops,
                        crops_lock,
                    )
                except Exception:
                    log.exception("Snapshot render failed for event %s", event_id)
                bump()

            tracks = _tracks_at(detections, idx, stride)
            for clip in list(active):
                if idx >= clip.end_idx:
                    continue
                try:
                    clip.feed(frame.copy(), tracks)
                except Exception:
                    log.exception("Clip frame failed for event %s", clip.event_id)

            for clip in [c for c in active if idx >= c.end_idx - 1]:
                active.remove(clip)
                try:
                    clip.finish(anchor)
                except Exception:
                    log.exception("Clip render failed for event %s", clip.event_id)
                bump()
    finally:
        for clip in active:
            try:
                clip.finish(anchor)
            except Exception:
                log.exception("Clip render failed for event %s", clip.event_id)
            bump()
        if queue:
            log.warning("%d clip(s) never reached their start frame", len(queue))
            for _ in queue:
                bump()

    _store_embeddings(crops)

    return {"events": events_written, "clips": len(clip_jobs), "duration_s": duration_s}


def _store_embeddings(crops: list) -> None:
    if not settings.clip_enabled or not crops:
        return
    try:
        from app.services.embeddings import get_embedder

        vecs = get_embedder().embed_images([img for _, img in crops])
        with SessionLocal() as db:
            db.add_all(
                Embedding(event_id=event_id, vec=vec.tolist())
                for (event_id, _), vec in zip(crops, vecs, strict=True)
            )
            db.commit()
        log.info("Stored %d frame embeddings", len(crops))
    except Exception:
        log.exception("Embedding computation failed — continuing without semantic search")


def _metric_snapshot_jobs(
    camera_id,
    zones,
    anchor: datetime,
    native_fps: float,
    stride: int,
    total_frames: int,
    existing: set[int],
) -> list[tuple[int, int, None]]:
    rows = []
    with SessionLocal() as db:
        camera_wide = select(Event.id, Event.ts_start).where(
            Event.camera_id == camera_id,
            Event.type == "occupancy",
            Event.zone_id.is_(None),
        )
        rows.append(
            db.execute(
                camera_wide.order_by(text("(attributes->>'count')::int DESC")).limit(1)
            ).first()
        )
        rows.append(db.execute(camera_wide.order_by(Event.ts_start.desc()).limit(1)).first())
        for zone in zones:
            rows.append(
                db.execute(
                    select(Event.id, Event.ts_start)
                    .where(Event.zone_id == zone.id, Event.type == "occupancy")
                    .order_by(text("(attributes->>'count')::int DESC"))
                    .limit(1)
                ).first()
            )
            if zone.kind == "checkout_area":
                rows.append(
                    db.execute(
                        select(Event.id, Event.ts_start)
                        .where(Event.zone_id == zone.id, Event.type == "queue_len")
                        .order_by(text("(attributes->>'queue_len')::int DESC"))
                        .limit(1)
                    ).first()
                )

    jobs: list[tuple[int, int, None]] = []
    for row in rows:
        if row is None or row.id in existing:
            continue
        idx = round((row.ts_start - anchor).total_seconds() * native_fps)
        idx = _processed_floor(min(idx, total_frames - 1), stride)
        jobs.append((row.id, idx, None))
        existing.add(row.id)
    return jobs


def _upsert_clip_row(event_id: int, ts: datetime, **fields) -> None:
    with _upsert_lock, SessionLocal() as db:
        clip = db.scalars(select(Clip).where(Clip.event_id == event_id)).first()
        if clip is None:
            clip = Clip(event_id=event_id, ts_start=ts)
            db.add(clip)
        for key, value in fields.items():
            setattr(clip, key, value)
        db.commit()


def _tracks_at(detections: dict[int, list[TrackedPerson]], frame_idx: int, stride: int):
    return detections.get(_processed_floor(frame_idx, stride), [])


def _crop_person(frame, track: TrackedPerson):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = track.xyxy
    pad_x, pad_y = 0.05 * (x2 - x1), 0.05 * (y2 - y1)
    x1 = max(0, int(x1 - pad_x))
    y1 = max(0, int(y1 - pad_y))
    x2 = min(w, int(x2 + pad_x))
    y2 = min(h, int(y2 + pad_y))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return frame[y1:y2, x1:x2].copy()


def _render_snapshot(
    frame,
    event_id: int,
    trigger_idx: int,
    track_id: int | None,
    anchor: datetime,
    native_fps: float,
    detections: dict[int, list[TrackedPerson]],
    zones,
    crops: list,
    crops_lock: threading.Lock,
) -> None:
    if track_id is not None and settings.clip_enabled:
        frame_tracks = detections.get(trigger_idx, [])
        track = next((t for t in frame_tracks if t.track_id == track_id), None)
        crop = _crop_person(frame, track) if track is not None else None
        if crop is None:
            crop = frame.copy()
        with crops_lock:
            crops.append((event_id, crop))

    apply_privacy_masks(frame, zones)
    draw_zones(frame, zones)
    frame = _snapshot_annotator.draw(frame, detections.get(trigger_idx, []))
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        return
    key = f"snapshots/{event_id}-{uuid.uuid4().hex[:8]}.jpg"
    storage.upload_bytes(key, jpeg.tobytes(), "image/jpeg")
    _upsert_clip_row(
        event_id, anchor + timedelta(seconds=trigger_idx / native_fps), snapshot_key=key
    )


class _ClipWriter:

    def __init__(self, event_id: int, start_idx: int, end_idx: int, fps: float, zones):
        self.event_id = event_id
        self.start_idx = start_idx
        self.end_idx = end_idx
        self.fps = fps
        self.zones = zones
        self.annotator = TrackAnnotator()
        self.people_counts: list[int] = []
        self._broken = False
        fd, self._out_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        self._proc = subprocess.Popen(
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
                f"{fps:.3f}",
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                self._out_path,
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def feed(self, frame, tracks) -> None:
        if self._broken or self._proc.stdin is None:
            return
        apply_privacy_masks(frame, self.zones)
        draw_zones(frame, self.zones)
        frame = self.annotator.draw(frame, tracks)
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return
        try:
            self._proc.stdin.write(jpeg.tobytes())
        except (BrokenPipeError, OSError):
            self._broken = True
            return
        self.people_counts.append(len(tracks))

    def finish(self, anchor: datetime) -> None:
        try:
            if self._proc.stdin is not None and not self._proc.stdin.closed:
                with contextlib.suppress(OSError):
                    self._proc.stdin.close()
            try:
                self._proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                log.error("ffmpeg timed out encoding clip for event %s", self.event_id)
                return
            if self._broken:
                return
            if len(self.people_counts) < self.fps:
                return
            if self._proc.returncode != 0:
                log.error(
                    "ffmpeg exited %s for event %s: %s",
                    self._proc.returncode,
                    self.event_id,
                    self._proc.stderr.read()[-500:],
                )
                return
            with open(self._out_path, "rb") as fh:
                mp4 = fh.read()
            if not mp4:
                return
            key = f"clips/{self.event_id}-{uuid.uuid4().hex[:8]}.mp4"
            storage.upload_bytes(key, mp4, "video/mp4")
            _upsert_clip_row(
                self.event_id,
                anchor + timedelta(seconds=self.start_idx / self.fps),
                storage_key=key,
                duration_s=round(len(self.people_counts) / self.fps, 1),
                people_frames=compress_presence(self.people_counts),
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(self._out_path)
