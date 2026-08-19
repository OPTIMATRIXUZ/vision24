import json
import logging
import statistics
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import cv2
import numpy as np
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from app.config import settings
from app.services.frames import prepare_vlm_frame, read_frames_at
from worker.types import DetectedObject, TrackedPerson
from worker.zone_engine import HYSTERESIS_S, MIN_TRACK_AGE_S, STALE_TRACK_S

log = logging.getLogger(__name__)

MAX_TRIP_S = 90.0
DOOR_VANISH_BUFFER = 0.04
TRUCK_EDGE_BUFFER = 0.03
STITCH_RADIUS = 0.2
STITCH_WINDOW_S = 2.5
STITCH_OVERLAP_S = 1.0
RECENT_WINDOW_S = 6.0
MIN_CROP_PX = 40
CROP_PAD = 0.12
PERSON_EXPAND_W = 0.40
PERSON_EXPAND_H = 0.25
MONTAGE_HEIGHT = 360
VLM_FRAMES_PER_TRIP = 3
CONF_FLOOR = 0.5


@dataclass
class Trip:
    track_id: int
    t_start: datetime
    frame_start: int
    t_end: datetime | None = None
    frame_end: int | None = None
    complete: bool = False
    stitched: bool = False
    candidates: list[tuple[int, tuple, float]] = field(default_factory=list)


@dataclass
class _Containment:

    inside: bool = False
    opposite_since: datetime | None = None

    def update(self, inside_now: bool, ts: datetime) -> bool:
        if inside_now == self.inside:
            self.opposite_since = None
            return False
        if self.opposite_since is None:
            self.opposite_since = ts
        if (ts - self.opposite_since).total_seconds() < HYSTERESIS_S:
            return False
        self.inside = inside_now
        self.opposite_since = None
        return True


@dataclass
class _TrackState:
    first_seen: datetime
    last_seen: datetime
    last_point: tuple[float, float]
    first_point: tuple[float, float] = (0.0, 0.0)
    first_frame: int = 0
    matured: bool = False
    truck: _Containment = field(default_factory=_Containment)
    door: _Containment = field(default_factory=_Containment)
    armed: bool = False
    trip: Trip | None = None
    recent: list[tuple[int, tuple, float, datetime]] = field(default_factory=list)


@dataclass
class _HandOff:

    ts: datetime
    point: tuple[float, float]
    armed: bool
    trip: Trip | None
    recent: list = field(default_factory=list)


class TripSegmenter:

    def __init__(self, truck_zones, door_zones, min_track_age_s: float = MIN_TRACK_AGE_S):
        def polys(rows):
            out = []
            for row in rows:
                try:
                    out.append(Polygon([(float(x), float(y)) for x, y in row.polygon]))
                except Exception:
                    log.exception("Bad polygon for zone %s", getattr(row, "id", "?"))
            return out

        truck_polys, door_polys = polys(truck_zones), polys(door_zones)
        self._truck = [prep(p) for p in truck_polys]
        self._truck_buffered = [prep(p.buffer(TRUCK_EDGE_BUFFER)) for p in truck_polys]
        self._door = [prep(p) for p in door_polys]
        self._door_buffered = [prep(p.buffer(DOOR_VANISH_BUFFER)) for p in door_polys]
        self.door_zone_id = door_zones[0].id if door_zones else None
        self.min_track_age_s = min_track_age_s
        self._tracks: dict[int, _TrackState] = {}
        self._handoffs: list[_HandOff] = []
        self.completed_count = 0
        self.incomplete_count = 0

    @property
    def active(self) -> bool:
        return bool(self._truck and self._door)

    def observe(
        self,
        tracks: list[TrackedPerson],
        ts: datetime,
        frame_idx: int,
        frame_w: int,
        frame_h: int,
    ) -> list[Trip]:
        done: list[Trip] = []
        seen: set[int] = set()
        for track in tracks:
            seen.add(track.track_id)
            point = track.foot_point(frame_w, frame_h)
            st = self._tracks.get(track.track_id)
            if st is None:
                st = self._tracks[track.track_id] = _TrackState(
                    first_seen=ts,
                    last_seen=ts,
                    last_point=point,
                    first_point=point,
                    first_frame=frame_idx,
                )
            st.last_seen = ts
            st.last_point = point
            if (ts - st.first_seen).total_seconds() < self.min_track_age_s:
                continue
            if not st.matured:
                st.matured = True
                self._claim_handoff(st, track.track_id)

            p = Point(*point)
            truck_flip_at = st.truck.opposite_since
            door_flip_at = st.door.opposite_since
            truck_changed = st.truck.update(any(z.contains(p) for z in self._truck), ts)
            door_changed = st.door.update(any(z.contains(p) for z in self._door), ts)

            if truck_changed and st.truck.inside:
                st.armed = True
                st.trip = None
            elif truck_changed and not st.truck.inside and st.armed and st.trip is None:
                start = truck_flip_at or ts
                st.trip = Trip(
                    track_id=track.track_id,
                    t_start=start,
                    frame_start=frame_idx,
                )
                st.trip.candidates.extend((f, b, a) for f, b, a, t in st.recent if t >= start)
            if door_changed and st.door.inside and st.trip is not None:
                st.trip.t_end = door_flip_at or ts
                st.trip.frame_end = frame_idx
                st.trip.complete = True
                done.append(st.trip)
                self.completed_count += 1
                st.trip = None
                st.armed = False

            if st.trip is not None:
                if (ts - st.trip.t_start).total_seconds() > MAX_TRIP_S:
                    st.trip = None
                    self.incomplete_count += 1
                elif not any(z.contains(p) for z in self._truck_buffered):
                    x1, y1, x2, y2 = track.xyxy
                    st.trip.candidates.append((frame_idx, tuple(track.xyxy), (x2 - x1) * (y2 - y1)))

            x1, y1, x2, y2 = track.xyxy
            st.recent.append((frame_idx, tuple(track.xyxy), (x2 - x1) * (y2 - y1), ts))
            cutoff = ts - timedelta(seconds=RECENT_WINDOW_S)
            while st.recent and st.recent[0][3] < cutoff:
                st.recent.pop(0)

        stale_cutoff = ts - timedelta(seconds=STALE_TRACK_S)
        for track_id, st in list(self._tracks.items()):
            if track_id in seen or st.last_seen > stale_cutoff:
                continue
            done.extend(self._resolve_vanished(st, allow_handoff=True))
            del self._tracks[track_id]

        expired = [h for h in self._handoffs if (ts - h.ts).total_seconds() > STITCH_WINDOW_S]
        for hand in expired:
            if hand.trip is not None:
                self.incomplete_count += 1
            self._handoffs.remove(hand)
        return done

    def flush(self) -> list[Trip]:
        done: list[Trip] = []
        for st in self._tracks.values():
            done.extend(self._resolve_vanished(st, allow_handoff=False))
        self._tracks.clear()
        for hand in self._handoffs:
            if hand.trip is not None:
                self.incomplete_count += 1
        self._handoffs.clear()
        return done

    def _resolve_vanished(self, st: _TrackState, *, allow_handoff: bool) -> list[Trip]:
        if st.trip is not None:
            p = Point(*st.last_point)
            if any(z.contains(p) for z in self._door_buffered):
                trip = st.trip
                st.trip = None
                trip.t_end = st.last_seen
                trip.frame_end = trip.candidates[-1][0] if trip.candidates else trip.frame_start
                trip.complete = True
                self.completed_count += 1
                return [trip]
        if not st.armed and st.trip is None:
            return []
        if allow_handoff and self._stitch(st):
            return []
        if allow_handoff:
            self._handoffs.append(
                _HandOff(
                    ts=st.last_seen,
                    point=st.last_point,
                    armed=st.armed,
                    trip=st.trip,
                    recent=st.recent,
                )
            )
        elif st.trip is not None:
            self.incomplete_count += 1
        return []

    def _stitch(self, vanished: _TrackState) -> bool:
        best, best_d = None, STITCH_RADIUS
        for other in self._tracks.values():
            if other is vanished or other.armed or other.trip is not None:
                continue
            if other.first_seen < vanished.last_seen - timedelta(seconds=STITCH_OVERLAP_S):
                continue
            d = (
                (other.first_point[0] - vanished.last_point[0]) ** 2
                + (other.first_point[1] - vanished.last_point[1]) ** 2
            ) ** 0.5
            if d <= best_d:
                best, best_d = other, d
        if best is None:
            return False
        succ_id = next(tid for tid, s in self._tracks.items() if s is best)
        self._transfer(
            _HandOff(
                ts=vanished.last_seen,
                point=vanished.last_point,
                armed=vanished.armed,
                trip=vanished.trip,
                recent=vanished.recent,
            ),
            best,
            succ_id,
        )
        vanished.trip = None
        return True

    def _claim_handoff(self, st: _TrackState, track_id: int) -> None:
        best, best_d = None, STITCH_RADIUS
        for hand in self._handoffs:
            if st.first_seen < hand.ts - timedelta(seconds=STITCH_OVERLAP_S):
                continue
            if (st.first_seen - hand.ts).total_seconds() > STITCH_WINDOW_S:
                continue
            d = (
                (st.first_point[0] - hand.point[0]) ** 2 + (st.first_point[1] - hand.point[1]) ** 2
            ) ** 0.5
            if d <= best_d:
                best, best_d = hand, d
        if best is None:
            return
        self._handoffs.remove(best)
        self._transfer(best, st, track_id)

    def _transfer(self, hand: _HandOff, succ: _TrackState, succ_id: int) -> None:
        succ.armed = True
        p = Point(*succ.last_point)
        succ.truck.inside = any(z.contains(p) for z in self._truck)
        seed = [(f, b, a) for f, b, a, _t in hand.recent]
        seed += [(f, b, a) for f, b, a, _t in succ.recent]
        if hand.trip is not None:
            hand.trip.stitched = True
            hand.trip.track_id = succ_id
            known = {c[0] for c in hand.trip.candidates}
            hand.trip.candidates.extend(c for c in seed if c[0] not in known)
            succ.trip = hand.trip
        elif not succ.truck.inside:
            succ.trip = Trip(
                track_id=succ_id,
                t_start=hand.ts,
                frame_start=succ.first_frame,
                stitched=True,
                candidates=seed,
            )


@dataclass
class ProductRef:
    id: str
    name: str
    units_per_package: int | None
    unit_label: str | None
    images: list
    median_aspect: float = 1.0


@dataclass
class ProductIndex:
    products: list[ProductRef]
    vecs: np.ndarray
    owner: np.ndarray


@dataclass
class _Keyframe:
    frame_idx: int
    person_xyxy: tuple
    packages: list[DetectedObject] = field(default_factory=list)
    crop_slots: list[int] = field(default_factory=list)
    half_slots: list[tuple[int, int] | None] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    class_ids: list[int] = field(default_factory=list)
    montage_img: np.ndarray | None = None
    scale: float = 1.0
    vlm_jpeg: bytes | None = None
    torso_slot: int | None = None


@dataclass
class TripResult:
    trip: Trip
    keyframes: list[_Keyframe] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    unmatched: int = 0
    count_total: int = 0
    count_basis: str = "median_keyframes"
    stack_suspect: bool = False
    verified_by_vlm: bool = False
    vlm_disagreement: bool = False
    count_unstable: bool = False
    has_unknown: bool = False
    best_crop: np.ndarray | None = None


def load_products(camera_id) -> list[ProductRef]:
    from sqlalchemy import select

    from app import storage
    from app.db import SessionLocal
    from app.models import Camera, ProductSample, ProductType

    out: list[ProductRef] = []
    with SessionLocal() as db:
        site_id = db.scalars(select(Camera.site_id).where(Camera.id == camera_id)).first()
        if site_id is None:
            return []
        rows = db.execute(
            select(ProductType, ProductSample)
            .join(ProductSample, ProductSample.product_type_id == ProductType.id)
            .where(ProductType.site_id == site_id)
            .order_by(ProductType.created_at, ProductSample.created_at)
        ).all()
    by_id: dict[str, ProductRef] = {}
    for product, sample in rows:
        ref = by_id.get(str(product.id))
        if ref is None:
            ref = by_id[str(product.id)] = ProductRef(
                id=str(product.id),
                name=product.name,
                units_per_package=product.units_per_package,
                unit_label=product.unit_label,
                images=[],
            )
            out.append(ref)
        try:
            data = storage.download_bytes(sample.storage_key)
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                ref.images.append(img)
        except Exception:
            log.exception("Could not load product sample %s", sample.id)
    products = [p for p in out if p.images]
    for p in products:
        p.median_aspect = statistics.median(img.shape[0] / img.shape[1] for img in p.images)
    return products


def build_product_index(products: list[ProductRef]) -> ProductIndex | None:
    images: list = []
    owner: list[int] = []
    for i, product in enumerate(products):
        for img in product.images:
            h, w = img.shape[:2]
            ch, cw = int(h * 0.1), int(w * 0.1)
            center = img[ch : h - ch, cw : w - cw]
            for variant in (img, center):
                if variant.size:
                    images.append(variant)
                    owner.append(i)
    if not images:
        return None
    from app.services.embeddings import get_embedder

    vecs = get_embedder().embed_images(images)
    return ProductIndex(products=products, vecs=vecs, owner=np.array(owner, dtype=int))


def classify(index: ProductIndex, crop_vecs: np.ndarray) -> list[tuple[int | None, float, float]]:
    results: list[tuple[int | None, float, float]] = []
    if crop_vecs.size == 0:
        return results
    sims = crop_vecs @ index.vecs.T
    n_products = len(index.products)
    for row in sims:
        per_product = np.full(n_products, -1.0, dtype=np.float32)
        for j in range(n_products):
            mask = index.owner == j
            if mask.any():
                per_product[j] = row[mask].max()
        order = np.argsort(per_product)[::-1]
        best, best_sim = int(order[0]), float(per_product[order[0]])
        second_sim = float(per_product[order[1]]) if n_products > 1 else -1.0
        margin = best_sim - second_sim
        if best_sim < settings.delivery_match_threshold:
            results.append((None, best_sim, margin))
        else:
            results.append((best, best_sim, margin))
    return results


def _expand_person(xyxy, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = xyxy
    pw, ph = (x2 - x1) * PERSON_EXPAND_W, (y2 - y1) * PERSON_EXPAND_H
    return (
        max(0, int(x1 - pw)),
        max(0, int(y1 - ph)),
        min(frame_w, int(x2 + pw)),
        min(frame_h, int(y2 + ph)),
    )


def _accept_package(det: DetectedObject, person_xyxy, frame_w: int, frame_h: int) -> bool:
    x1, y1, x2, y2 = det.xyxy
    area = (x2 - x1) * (y2 - y1)
    px1, py1, px2, py2 = person_xyxy
    person_area = max(1.0, (px2 - px1) * (py2 - py1))
    if area > 0.6 * person_area:
        return False
    if area < 0.004 * frame_w * frame_h:
        return False
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    person_w = max(1.0, px2 - px1)
    if not (px1 - 0.15 * person_w <= cx <= px2 + 0.15 * person_w):
        return False
    person_h = max(1.0, py2 - py1)
    return py1 + 0.15 * person_h <= cy <= py1 + 0.90 * person_h


def _square_pad(crop: np.ndarray) -> np.ndarray:
    ch, cw = crop.shape[:2]
    if ch == cw:
        return crop.copy()
    diff = abs(ch - cw)
    top = bottom = left = right = 0
    if ch > cw:
        left, right = diff // 2, diff - diff // 2
    else:
        top, bottom = diff // 2, diff - diff // 2
    return cv2.copyMakeBorder(crop, top, bottom, left, right, cv2.BORDER_REPLICATE)


def _package_crop(frame, xyxy) -> np.ndarray | None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = xyxy
    pw, ph = (x2 - x1) * CROP_PAD, (y2 - y1) * CROP_PAD
    x1, y1 = max(0, int(x1 - pw)), max(0, int(y1 - ph))
    x2, y2 = min(w, int(x2 + pw)), min(h, int(y2 + ph))
    if x2 - x1 < MIN_CROP_PX or y2 - y1 < MIN_CROP_PX:
        return None
    return _square_pad(frame[y1:y2, x1:x2])


def _stack_halves(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    h = crop.shape[0]
    if h < 2 * MIN_CROP_PX:
        return None
    return _square_pad(crop[: h // 2]), _square_pad(crop[h // 2 :])


def _torso_crop(frame, person_xyxy) -> np.ndarray | None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = person_xyxy
    pw = (x2 - x1) * 0.4
    x1, x2 = max(0, int(x1 - pw)), min(w, int(x2 + pw))
    y1, y2 = max(0, int(y1)), min(h, int(y1 + (y2 - y1) * 0.6))
    if x2 - x1 < MIN_CROP_PX or y2 - y1 < MIN_CROP_PX:
        return None
    return frame[y1:y2, x1:x2].copy()


def _select_candidates(trip: Trip, stride: int) -> list[tuple[int, tuple]]:
    min_gap = stride * 2
    picked: list[tuple[int, tuple]] = []
    for frame_idx, xyxy, _area in sorted(trip.candidates, key=lambda c: c[0], reverse=True):
        if any(abs(frame_idx - p[0]) < min_gap for p in picked):
            continue
        picked.append((frame_idx, xyxy))
        if len(picked) >= max(1, settings.delivery_keyframes):
            break
    return sorted(picked)


@dataclass
class _Candidate:

    frame_idx: int
    person_xyxy: tuple
    frame_w: int
    frame_h: int
    region: tuple
    region_crop: np.ndarray
    montage_img: np.ndarray
    vlm_jpeg: bytes | None


def analyze_trips(
    path: str,
    trips: list[Trip],
    zones,
    stride: int,
    want_vlm: bool,
) -> tuple[list[TripResult], list]:
    from worker.package_detector import PackageDetector

    results = [TripResult(trip=t) for t in trips]
    crops: list = []

    wanted: dict[int, list[tuple[int, tuple]]] = {}
    for i, result in enumerate(results):
        for frame_idx, xyxy in _select_candidates(result.trip, stride):
            wanted.setdefault(frame_idx, []).append((i, xyxy))

    per_result: list[list[_Candidate]] = [[] for _ in results]
    for frame_idx, frame in read_frames_at(path, wanted):
        frame_h, frame_w = frame.shape[:2]

        extracted = []
        for i, xyxy in wanted[frame_idx]:
            region = _expand_person(xyxy, frame_w, frame_h)
            rx1, ry1, rx2, ry2 = region
            region_crop = frame[ry1:ry2, rx1:rx2].copy()
            extracted.append((i, xyxy, region, region_crop))
        jpeg = prepare_vlm_frame(frame, zones)
        scale = MONTAGE_HEIGHT / frame_h
        montage_img = cv2.resize(frame, (max(1, int(frame_w * scale)), MONTAGE_HEIGHT))
        for i, xyxy, region, region_crop in extracted:
            per_result[i].append(
                _Candidate(
                    frame_idx=frame_idx,
                    person_xyxy=xyxy,
                    frame_w=frame_w,
                    frame_h=frame_h,
                    region=region,
                    region_crop=region_crop,
                    montage_img=montage_img,
                    vlm_jpeg=jpeg if want_vlm else None,
                )
            )

    detector = PackageDetector(device=settings.yolo_device)
    try:
        for result, chosen in zip(results, per_result, strict=True):
            sharpest = chosen[-1] if chosen else None

            for cand in chosen:
                kf = _Keyframe(frame_idx=cand.frame_idx, person_xyxy=cand.person_xyxy)
                rx1, ry1, _rx2, _ry2 = cand.region
                px1, py1, px2, py2 = cand.person_xyxy
                local_person = (px1 - rx1, py1 - ry1, px2 - rx1, py2 - ry1)
                if cand.region_crop.size:
                    for det in detector.detect(cand.region_crop):
                        mapped = DetectedObject(
                            xyxy=(
                                det.xyxy[0] + rx1,
                                det.xyxy[1] + ry1,
                                det.xyxy[2] + rx1,
                                det.xyxy[3] + ry1,
                            ),
                            conf=det.conf,
                            class_name=det.class_name,
                        )
                        if not _accept_package(
                            mapped, cand.person_xyxy, cand.frame_w, cand.frame_h
                        ):
                            continue
                        crop = _package_crop(cand.region_crop, det.xyxy)
                        if crop is None:
                            continue
                        kf.packages.append(mapped)
                        kf.crop_slots.append(len(crops))
                        crops.append(crop)
                        halves = _stack_halves(crop)
                        if halves is None:
                            kf.half_slots.append(None)
                        else:
                            kf.half_slots.append((len(crops), len(crops) + 1))
                            crops.extend(halves)
                if cand is sharpest:
                    torso = _torso_crop(cand.region_crop, local_person)
                    if torso is not None:
                        kf.torso_slot = len(crops)
                        crops.append(torso)

                vlm_frames = sum(1 for k in result.keyframes if k.vlm_jpeg)
                if want_vlm and vlm_frames < VLM_FRAMES_PER_TRIP:
                    kf.vlm_jpeg = cand.vlm_jpeg
                kf.scale = MONTAGE_HEIGHT / cand.frame_h
                kf.montage_img = cand.montage_img
                result.keyframes.append(kf)
    finally:
        detector.close()
    return results, crops


def _detection_units(
    verdicts: list, slot: int, halves: tuple[int, int] | None
) -> tuple[list[tuple[int | None, float, float]], bool]:
    if halves is not None:
        top, bottom = verdicts[halves[0]], verdicts[halves[1]]
        if (
            top[0] is not None
            and bottom[0] is not None
            and top[0] != bottom[0]
            and top[2] >= settings.delivery_margin
            and bottom[2] >= settings.delivery_margin
        ):
            return [top, bottom], True
    return [verdicts[slot]], False


def _kf_halves(kf: _Keyframe, i: int) -> tuple[int, int] | None:
    return kf.half_slots[i] if i < len(kf.half_slots) else None


def _label_keyframes(results: list[TripResult], index: ProductIndex, verdicts: list) -> None:
    unknown_class = len(index.products)
    for result in results:
        for kf in result.keyframes:
            for i, slot in enumerate(kf.crop_slots):
                units, split = _detection_units(verdicts, slot, _kf_halves(kf, i))
                if split:
                    names = "+".join(index.products[u[0]].name for u in units)
                    kf.labels.append(f"{names} {min(u[1] for u in units):.2f}")
                    kf.class_ids.append(units[0][0])
                    continue
                pidx, sim, _margin = units[0]
                name = index.products[pidx].name if pidx is not None else "?"
                kf.labels.append(f"{name} {sim:.2f}")
                kf.class_ids.append(pidx if pidx is not None else unknown_class)


def _aggregate(
    result: TripResult, index: ProductIndex, verdicts: list, torso_verdicts: dict
) -> None:
    kfs = result.keyframes
    per_kf_units: list[list[tuple]] = []
    per_kf_dets: list[list[DetectedObject]] = []
    for kf in kfs:
        units_in_kf: list[tuple] = []
        dets_in_kf: list[DetectedObject] = []
        for i, (det, slot) in enumerate(zip(kf.packages, kf.crop_slots, strict=True)):
            units, split = _detection_units(verdicts, slot, _kf_halves(kf, i))
            if split:
                result.stack_suspect = True
            units_in_kf.extend(units)
            dets_in_kf.extend([det] * len(units))
        per_kf_units.append(units_in_kf)
        per_kf_dets.append(dets_in_kf)

    detected = [len(units) for units in per_kf_units if units]
    if len(detected) >= 2:
        result.count_total = round(float(np.median(detected)))
        result.count_unstable = (max(detected) - min(detected)) >= 2
    elif len(detected) == 1:
        result.count_total = detected[0]
        result.count_basis = "single_keyframe"
        result.has_unknown = True
    else:
        result.count_total = 0

    per_type_frames: dict[int, list[int]] = {}
    per_type_sims: dict[int, list[float]] = {}
    for units_in_kf, dets_in_kf in zip(per_kf_units, per_kf_dets, strict=True):
        frame_counts: dict[int, int] = {}
        for (pidx, sim, margin), det in zip(units_in_kf, dets_in_kf, strict=True):
            if pidx is None or margin < settings.delivery_margin:
                result.has_unknown = True
                continue
            frame_counts[pidx] = frame_counts.get(pidx, 0) + 1
            per_type_sims.setdefault(pidx, []).append(sim)
            x1, y1, x2, y2 = det.xyxy
            if (y2 - y1) / max(1.0, x2 - x1) > 1.6 * index.products[pidx].median_aspect:
                result.stack_suspect = True
        for pidx, n in frame_counts.items():
            per_type_frames.setdefault(pidx, []).append(n)

    min_frames = 2 if len(kfs) >= 2 else 1
    for pidx, frame_counts in per_type_frames.items():
        if len(frame_counts) < min_frames:
            result.has_unknown = True
            continue
        count = round(float(np.median(frame_counts)))
        if count < 1:
            continue
        sims = per_type_sims.get(pidx, [0.0])
        confidence = max(0.0, min(1.0, (float(np.mean(sims)) - CONF_FLOOR) / (1 - CONF_FLOOR)))
        product = index.products[pidx]
        result.items.append(
            {
                "product_type_id": product.id,
                "product_name": product.name,
                "count": count,
                "confidence": round(confidence, 2),
            }
        )

    if not result.items and result.count_total == 0 and result.trip.complete:
        result.count_total = 1
        result.count_basis = "torso_fallback"
        verdict = torso_verdicts.get(id(result))
        if verdict is not None and verdict[0] is not None:
            product = index.products[verdict[0]]
            confidence = max(0.0, min(1.0, (verdict[1] - CONF_FLOOR) / (1 - CONF_FLOOR)))
            result.items.append(
                {
                    "product_type_id": product.id,
                    "product_name": product.name,
                    "count": 1,
                    "confidence": round(confidence * 0.5, 2),
                }
            )
    typed = sum(item["count"] for item in result.items)
    result.unmatched = max(0, result.count_total - typed)


VLM_BATCH_FRAMES_PER_TRIP = 2
VLM_BATCH_FRAME_BUDGET = 10


def _verify_with_vlm(uncertain: list[TripResult], index: ProductIndex) -> None:
    from app.services.ai.provider import ImagePart, Msg, TextPart, get_provider
    from app.services.frames import _encode

    included: list[tuple[TripResult, list[bytes]]] = []
    for result in uncertain:
        frames = [kf.vlm_jpeg for kf in result.keyframes if kf.vlm_jpeg]
        frames = frames[:VLM_BATCH_FRAMES_PER_TRIP]
        if not frames:
            continue
        used = sum(len(f) for _r, f in included)
        if used + len(frames) > VLM_BATCH_FRAME_BUDGET:
            break
        included.append((result, frames))
    if not included:
        return

    names = [p.name for p in index.products]
    parts: list = [
        TextPart(
            "A worker is carrying packages from a delivery truck into a shop. "
            f"The known products are: {', '.join(names)}. "
            f"Below are frames from {len(included)} separate carrying trips. For EACH trip, "
            "count how many distinct packages the person is carrying and name the product "
            "of each. Answer with strict JSON only, one entry per trip: "
            '[{"trip": <trip number>, "count": <int>, '
            '"types": ["<product name or unknown>", ...]}, ...]'
        )
    ]
    for n, (_result, frames) in enumerate(included, start=1):
        parts.append(TextPart(f"Trip {n}:"))
        parts.extend(ImagePart(data=jpeg) for jpeg in frames)
    for product in index.products[:3]:
        sample_jpeg = _encode(product.images[0])
        if sample_jpeg:
            parts.append(TextPart(f"Reference photo of '{product.name}':"))
            parts.append(ImagePart(data=sample_jpeg))

    resp = get_provider().generate(
        system="You count objects in CCTV frames. Answer with strict JSON only.",
        messages=[Msg(role="user", parts=parts)],
    )
    raw = resp.text[resp.text.find("[") : resp.text.rfind("]") + 1]
    entries = json.loads(raw)
    for result, _frames in included:
        result.verified_by_vlm = True
    for entry in entries:
        try:
            trip_no = int(entry.get("trip", 0))
            if not 1 <= trip_no <= len(included):
                continue
            result = included[trip_no - 1][0]
            count = int(entry.get("count", -1))
            types = [str(t) for t in entry.get("types", [])]
        except (TypeError, ValueError, AttributeError):
            continue
        _apply_vlm_verdict(result, count, types, index)


def _apply_vlm_verdict(
    result: TripResult, count: int, types: list[str], index: ProductIndex
) -> None:
    weak_count = result.count_unstable or result.count_basis in (
        "single_keyframe",
        "torso_fallback",
    )
    if 0 <= count <= 12 and weak_count:
        result.count_total = count
        result.count_basis = "vlm"

    by_name = {p.name.lower(): p for p in index.products}
    vlm_counts: dict[str, int] = {}
    for t in types:
        vlm_counts[t.strip().lower()] = vlm_counts.get(t.strip().lower(), 0) + 1

    clip_names = {item["product_name"].lower() for item in result.items}
    extra_budget = 0
    if 0 <= count <= 12 and count > result.count_total and clip_names <= set(vlm_counts):
        extra_budget = count - result.count_total

    for name_lower, n in vlm_counts.items():
        product = by_name.get(name_lower)
        if product is None:
            continue
        existing = next(
            (item for item in result.items if item["product_name"].lower() == name_lower), None
        )
        if existing is not None:
            if existing["count"] != n:
                result.vlm_disagreement = True
            continue
        available = result.unmatched + extra_budget
        if available > 0:
            take = min(n, available)
            from_unmatched = min(take, result.unmatched)
            result.unmatched -= from_unmatched
            extra = take - from_unmatched
            extra_budget -= extra
            result.count_total += extra
            result.items.append(
                {
                    "product_type_id": product.id,
                    "product_name": product.name,
                    "count": take,
                    "confidence": 0.5,
                }
            )


def run_delivery_pipeline(
    camera_id,
    path: str,
    trips: list[Trip],
    zones,
    stride: int,
    incomplete_trips: int,
    door_zone_id,
    end_ts: datetime,
) -> int | None:
    try:
        if not settings.delivery_enabled:
            return None
        trips = [t for t in trips if t.complete]
        products = load_products(camera_id)
        if not products:
            if trips:
                log.info("Delivery trips found but no product samples — skipping counting")
            return None
        if not trips and incomplete_trips == 0:
            return None

        from app.services.ai.provider import is_configured

        want_vlm = settings.delivery_vlm_verify and is_configured()
        results, crops = analyze_trips(path, trips, zones, stride, want_vlm)

        index = build_product_index(products)
        if index is None:
            return None
        from app.services.embeddings import get_embedder

        crop_vecs = (
            get_embedder().embed_images(crops) if crops else np.zeros((0, 512), dtype=np.float32)
        )
        verdicts = classify(index, crop_vecs)
        _label_keyframes(results, index, verdicts)

        torso_verdicts: dict[int, tuple] = {}
        for result in results:
            if any(kf.crop_slots for kf in result.keyframes):
                continue
            for kf in result.keyframes:
                if kf.torso_slot is not None:
                    torso_verdicts[id(result)] = verdicts[kf.torso_slot]
                    break

        for result in results:
            _aggregate(result, index, verdicts, torso_verdicts)

        for result in results:
            best_slot, best_score = None, 0.0
            for kf in result.keyframes:
                for det, slot in zip(kf.packages, kf.crop_slots, strict=True):
                    _pidx, sim, _margin = verdicts[slot]
                    x1, y1, x2, y2 = det.xyxy
                    score = max(sim, 0.05) * ((x2 - x1) * (y2 - y1)) ** 0.5
                    if score > best_score:
                        best_slot, best_score = slot, score
            if best_slot is None:
                for kf in result.keyframes:
                    if kf.torso_slot is not None:
                        best_slot = kf.torso_slot
                        break
            result.best_crop = crops[best_slot] if best_slot is not None else None

        if want_vlm:
            uncertain = [
                r
                for r in results
                if r.count_unstable
                or r.has_unknown
                or r.stack_suspect
                or r.count_basis == "torso_fallback"
            ]
            if uncertain:
                try:
                    _verify_with_vlm(uncertain[: settings.delivery_vlm_max_trips], index)
                except Exception:
                    log.exception("VLM verification failed — keeping CLIP results")

        return _persist(camera_id, results, incomplete_trips, door_zone_id, end_ts)
    except Exception:
        log.exception("Delivery pipeline failed — base analysis is unaffected")
        return None


def _persist(
    camera_id, results: list[TripResult], incomplete: int, door_zone_id, end_ts: datetime
) -> int:
    from app import storage
    from app.db import SessionLocal
    from app.models import Clip, Event
    from worker.annotate import build_trip_montage

    written = 0
    totals: dict[str, int] = {}
    unknown_total = 0
    with SessionLocal() as db:
        for n, result in enumerate(results, start=1):
            trip = result.trip
            row = Event(
                camera_id=camera_id,
                zone_id=door_zone_id,
                type="delivery_trip",
                track_id=trip.track_id,
                ts_start=trip.t_start,
                ts_end=trip.t_end,
                attributes={
                    "direction": "in",
                    "items": result.items,
                    "unmatched": result.unmatched,
                    "count_total": result.count_total,
                    "count_basis": result.count_basis,
                    "verified_by_vlm": result.verified_by_vlm,
                    "vlm_disagreement": result.vlm_disagreement,
                    "stack_suspect": result.stack_suspect,
                    "stitched": trip.stitched,
                    "keyframes": [kf.frame_idx for kf in result.keyframes],
                },
            )
            db.add(row)
            db.flush()
            written += 1
            for item in result.items:
                totals[item["product_name"]] = totals.get(item["product_name"], 0) + item["count"]
            unknown_total += result.unmatched

            panels = []
            for kf in result.keyframes:
                if kf.montage_img is None:
                    continue
                dets = [
                    (tuple(v * kf.scale for v in det.xyxy), cid, label)
                    for det, cid, label in zip(kf.packages, kf.class_ids, kf.labels, strict=True)
                ]
                panels.append((kf.montage_img, dets))
            summary = ", ".join(f"{i['count']}x {i['product_name']}" for i in result.items)
            hud = f"Trip {n} | {trip.t_start:%H:%M:%S} | " + (
                summary or f"{result.count_total} package(s)"
            )
            montage = build_trip_montage(panels, hud)
            if montage is not None:
                ok, jpeg = cv2.imencode(".jpg", montage, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    key = f"snapshots/{row.id}-{uuid_mod.uuid4().hex[:8]}.jpg"
                    storage.upload_bytes(key, jpeg.tobytes(), "image/jpeg")
                    db.add(Clip(event_id=row.id, ts_start=trip.t_start, snapshot_key=key))

            if result.best_crop is not None:
                ok, jpeg = cv2.imencode(".jpg", result.best_crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if ok:
                    crop_key = f"snapshots/{row.id}-crop-{uuid_mod.uuid4().hex[:8]}.jpg"
                    storage.upload_bytes(crop_key, jpeg.tobytes(), "image/jpeg")
                    row.attributes = {**row.attributes, "crop_key": crop_key}

        if results or incomplete:
            db.add(
                Event(
                    camera_id=camera_id,
                    zone_id=None,
                    type="delivery_summary",
                    ts_start=end_ts,
                    attributes={
                        "counts": totals,
                        "trips": len(results),
                        "incomplete_trips": incomplete,
                        "unknown_packages": unknown_total,
                    },
                )
            )
            written += 1
        db.commit()
    log.info(
        "Delivery counting: %d trips, %d incomplete, totals=%s", len(results), incomplete, totals
    )
    return written
