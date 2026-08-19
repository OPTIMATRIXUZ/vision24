import logging
import os
import threading
import time as time_mod
from datetime import UTC, datetime

import cv2
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Alert, AlertRule, Event, Zone
from app.services import telegram
from app.services.frames import apply_privacy_masks
from worker.alert_engine import AlertEngine
from worker.annotate import TrackAnnotator
from worker.camera_spec import CameraSpec
from worker.clip_writer import ClipWriter
from worker.detector import PersonDetector
from worker.motion import MotionGate
from worker.procguard import is_forked_child
from worker.zone_engine import ZoneEngine

CONFIG_RELOAD_S = 30.0
STATS_LOG_S = 5.0
STALL_READS = 25
MAX_DETECT_FAILURES = 30
OPEN_BACKOFF_MAX_S = 10.0


class CameraWorker:

    def __init__(self, spec: CameraSpec, fps: int | None = None, show: bool = False):
        self.spec = spec
        self.fps = fps or settings.worker_fps
        self.show = show
        self.log = logging.getLogger(f"worker[{spec.name}]")
        self._stop = threading.Event()

        self.state = "starting"
        self.started_at: datetime | None = None
        self.observed_fps = 0.0
        self.track_count = 0
        self.event_count = 0
        self.last_event_at: datetime | None = None

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()


    def _reload_config(self, zone_engine: ZoneEngine, alert_engine: AlertEngine) -> list[Zone]:
        with SessionLocal() as db:
            zones = db.scalars(select(Zone).where(Zone.camera_id == self.spec.id)).all()
            zone_engine.update_zones(zones)
            rules = db.execute(
                select(AlertRule, Zone.name)
                .join(Zone, AlertRule.zone_id == Zone.id)
                .where(AlertRule.is_active, Zone.camera_id == self.spec.id)
            ).all()
            alert_engine.update_rules(rules)
            return zones

    def _open_capture(self) -> cv2.VideoCapture | None:
        backoff = 1.0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.spec.source, cv2.CAP_FFMPEG)
            if cap.isOpened():
                self.log.info("Capture opened: %s", self.spec.source)
                self.state = "running"
                return cap
            cap.release()
            self.state = "reconnecting"
            self.log.warning("Could not open %s, retrying in %.0fs", self.spec.source, backoff)
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, OPEN_BACKOFF_MAX_S)
        return None


    def _persist_events(self, db, events) -> None:
        for spec in events:
            row = Event(
                camera_id=self.spec.id,
                zone_id=spec.zone_id,
                type=spec.type,
                track_id=spec.track_id,
                ts_start=spec.ts_start,
                ts_end=spec.ts_end,
                attributes=spec.attributes,
            )
            db.add(row)
            db.flush()
            spec.db_id = row.id
        db.commit()

    def _handle_events(self, events, clip_writer: ClipWriter, alert_engine: AlertEngine) -> None:
        with SessionLocal() as db:
            self._persist_events(db, events)
            for spec in events:
                if spec.type == "entry":
                    clip_writer.request_snapshot(spec.db_id, spec.ts_start)
                    if spec.record_clip:
                        clip_writer.request_clip(spec.db_id, spec.ts_start)
                for alert in alert_engine.process(spec):
                    db.add(
                        Alert(
                            rule_id=alert.rule_id,
                            event_id=spec.db_id,
                            triggered_at=alert.triggered_at,
                            value=alert.value,
                            message=alert.message,
                        )
                    )
                    db.commit()
                    clip_writer.request_clip(spec.db_id, spec.ts_start)
                    clip_writer.request_snapshot(spec.db_id, spec.ts_start)
                    telegram.send_alert(
                        f"⚠️ Vision24 [{self.spec.name}]: {alert.message}",
                        camera_id=self.spec.id,
                        event_id=spec.db_id,
                    )
                    self.log.info("ALERT: %s", alert.message)
        self.event_count += len(events)
        self.last_event_at = events[-1].ts_start


    def run(self) -> None:
        detector = PersonDetector(device=settings.yolo_device)
        zone_engine = ZoneEngine(min_track_age_s=settings.min_track_age_s)
        alert_engine = AlertEngine()
        clip_writer = ClipWriter(fps=self.fps)
        annotator = TrackAnnotator()
        gate = (
            MotionGate(min_ratio=settings.motion_min_ratio, pixel_delta=settings.motion_pixel_delta)
            if settings.motion_gate_enabled
            else None
        )
        zones = self._reload_config(zone_engine, alert_engine)

        cap = self._open_capture()
        if cap is None:
            self.state = "stopped"
            return
        self.started_at = datetime.now(UTC)

        frame_interval = 1.0 / self.fps
        last_processed = 0.0
        last_reload = time_mod.monotonic()
        last_stats = time_mod.monotonic()
        processed = 0
        read_failures = 0
        detect_failures = 0
        active_tracks = False
        skipped_frames = 0
        tracks = []

        try:
            while not self._stop.is_set():
                if is_forked_child():
                    os._exit(0)
                if not cap.grab():
                    read_failures += 1
                    if read_failures >= STALL_READS:
                        self.log.warning("Stream stalled, reconnecting")
                        cap.release()
                        cap = self._open_capture()
                        if cap is None:
                            break
                        read_failures = 0
                    time_mod.sleep(0.05)
                    continue
                read_failures = 0

                now_mono = time_mod.monotonic()
                if now_mono - last_processed < frame_interval:
                    continue
                last_processed = now_mono

                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue

                ts = datetime.now(UTC)
                frame_h, frame_w = frame.shape[:2]

                moving = gate.motion(frame) if gate is not None else True
                if not moving and not active_tracks:
                    tracks = []
                    skipped_frames += 1
                else:
                    try:
                        tracks = detector.track(frame)
                    except Exception:
                        detect_failures += 1
                        self.log.exception(
                            "Detection failed on frame (%d consecutive)", detect_failures
                        )
                        if detect_failures >= MAX_DETECT_FAILURES:
                            self.log.error(
                                "Detection has failed %d times in a row — giving up so the "
                                "supervisor can restart this camera with a fresh detector.",
                                detect_failures,
                            )
                            break
                        continue
                    detect_failures = 0
                    active_tracks = bool(tracks)

                apply_privacy_masks(frame, zones)
                frame = annotator.draw(frame, tracks)
                clip_writer.add_frame(ts, frame, people=len(tracks))
                events = zone_engine.process(tracks, ts, frame_w, frame_h)
                if events:
                    self._handle_events(events, clip_writer, alert_engine)

                processed += 1
                self.track_count = len(tracks)
                if now_mono - last_stats >= STATS_LOG_S:
                    self.observed_fps = processed / (now_mono - last_stats)
                    self.log.info(
                        "fps=%.1f tracks=%s events_total=%d skipped=%d",
                        self.observed_fps,
                        sorted(t.track_id for t in tracks),
                        self.event_count,
                        skipped_frames,
                    )
                    processed = 0
                    skipped_frames = 0
                    last_stats = now_mono

                if now_mono - last_reload >= CONFIG_RELOAD_S:
                    zones = self._reload_config(zone_engine, alert_engine)
                    last_reload = now_mono

                if self.show:
                    cv2.imshow(f"vision24 — {self.spec.name}", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        finally:
            if cap is not None:
                cap.release()
            self.state = "stopped"
            try:
                closing = zone_engine.flush(datetime.now(UTC))
                if closing:
                    self._handle_events(closing, clip_writer, alert_engine)
            except Exception:
                self.log.exception("Failed to flush open visits on shutdown")
