import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from worker.types import TrackedPerson

log = logging.getLogger(__name__)

HYSTERESIS_S = 0.5
MIN_TRACK_AGE_S = 0.8
STALE_TRACK_S = 2.0
OCCUPANCY_PERIOD_S = 1.0
QUEUE_PERIOD_S = 2.0


@dataclass
class ZoneRuntime:
    id: uuid.UUID
    name: str
    kind: str
    record_clips: bool
    polygon: object


@dataclass
class EventSpec:
    type: str
    zone_id: uuid.UUID | None
    track_id: int | None
    ts_start: datetime
    ts_end: datetime | None = None
    attributes: dict = field(default_factory=dict)
    record_clip: bool = False


@dataclass
class TrackZoneState:
    inside: bool = False
    opposite_since: datetime | None = None
    entered_at: datetime | None = None


class ZoneEngine:
    def __init__(self, min_track_age_s: float = MIN_TRACK_AGE_S):
        self.zones: list[ZoneRuntime] = []
        self.state: dict[tuple[int, uuid.UUID], TrackZoneState] = {}
        self.track_first_seen: dict[int, datetime] = {}
        self.track_last_seen: dict[int, datetime] = {}
        self.min_track_age_s = min_track_age_s
        self.last_presence: int = 0
        self._last_occupancy: datetime | None = None
        self._last_queue: datetime | None = None

    def update_zones(self, zone_rows) -> None:
        zones = []
        for row in zone_rows:
            try:
                poly = Polygon([(float(x), float(y)) for x, y in row.polygon])
                zones.append(
                    ZoneRuntime(
                        id=row.id,
                        name=row.name,
                        kind=row.kind,
                        record_clips=row.record_clips,
                        polygon=prep(poly),
                    )
                )
            except Exception:
                log.exception("Bad polygon for zone %s", row.id)
        removed = {z.id for z in self.zones} - {z.id for z in zones}
        if removed:
            self.state = {k: v for k, v in self.state.items() if k[1] not in removed}
        self.zones = zones

    def process(
        self, tracks: list[TrackedPerson], ts: datetime, frame_w: int, frame_h: int
    ) -> list[EventSpec]:
        events: list[EventSpec] = []
        seen_ids = set()

        for track in tracks:
            seen_ids.add(track.track_id)
            self.track_first_seen.setdefault(track.track_id, ts)
            self.track_last_seen[track.track_id] = ts

        mature = [
            t
            for t in tracks
            if (ts - self.track_first_seen[t.track_id]).total_seconds() >= self.min_track_age_s
        ]
        self.last_presence = len(mature)

        for track in mature:
            point = Point(*track.foot_point(frame_w, frame_h))
            for zone in self.zones:
                inside = zone.polygon.contains(point)
                key = (track.track_id, zone.id)
                st = self.state.setdefault(key, TrackZoneState())
                if inside == st.inside:
                    st.opposite_since = None
                    continue
                if st.opposite_since is None:
                    st.opposite_since = ts
                if (ts - st.opposite_since).total_seconds() < HYSTERESIS_S:
                    continue
                st.inside = inside
                st.opposite_since = None
                if inside:
                    st.entered_at = ts
                    events.append(
                        EventSpec(
                            type="entry",
                            zone_id=zone.id,
                            track_id=track.track_id,
                            ts_start=ts,
                            record_clip=zone.record_clips,
                        )
                    )
                else:
                    events.extend(self._close_visit(st, zone, track.track_id, ts))

        stale_cutoff = ts - timedelta(seconds=STALE_TRACK_S)
        for (track_id, zone_id), st in list(self.state.items()):
            last_seen = self.track_last_seen.get(track_id)
            if track_id in seen_ids or last_seen is None or last_seen > stale_cutoff:
                continue
            if st.inside:
                zone = next((z for z in self.zones if z.id == zone_id), None)
                if zone is not None:
                    events.extend(self._close_visit(st, zone, track_id, ts))
            del self.state[(track_id, zone_id)]

        for track_id, last_seen in list(self.track_last_seen.items()):
            if track_id not in seen_ids and last_seen < stale_cutoff:
                self.track_last_seen.pop(track_id, None)
                self.track_first_seen.pop(track_id, None)

        events.extend(self._periodic_samples(mature, ts))
        return events

    def flush(self, ts: datetime) -> list[EventSpec]:
        events: list[EventSpec] = []
        for (track_id, zone_id), st in list(self.state.items()):
            if not st.inside:
                continue
            zone = next((z for z in self.zones if z.id == zone_id), None)
            if zone is None:
                continue
            closed = self._close_visit(st, zone, track_id, ts)
            for spec in closed:
                if spec.type == "dwell":
                    spec.attributes["truncated"] = True
            events.extend(closed)
        self.state.clear()
        self.track_first_seen.clear()
        self.track_last_seen.clear()
        return events

    def _close_visit(self, st: TrackZoneState, zone: ZoneRuntime, track_id: int, ts: datetime):
        events = [EventSpec(type="exit", zone_id=zone.id, track_id=track_id, ts_start=ts)]
        if st.entered_at is not None:
            dwell_s = (ts - st.entered_at).total_seconds()
            events.append(
                EventSpec(
                    type="dwell",
                    zone_id=zone.id,
                    track_id=track_id,
                    ts_start=st.entered_at,
                    ts_end=ts,
                    attributes={"dwell_s": round(dwell_s, 1)},
                )
            )
        st.inside = False
        st.entered_at = None
        return events

    def _zone_count(self, zone_id: uuid.UUID) -> int:
        return sum(1 for (_, zid), st in self.state.items() if zid == zone_id and st.inside)

    def zone_counts(self) -> dict[uuid.UUID, int]:
        return {z.id: self._zone_count(z.id) for z in self.zones}

    def _periodic_samples(self, tracks: list[TrackedPerson], ts: datetime) -> list[EventSpec]:
        events: list[EventSpec] = []
        if (
            self._last_occupancy is None
            or (ts - self._last_occupancy).total_seconds() >= OCCUPANCY_PERIOD_S
        ):
            self._last_occupancy = ts
            events.append(
                EventSpec(
                    type="occupancy",
                    zone_id=None,
                    track_id=None,
                    ts_start=ts,
                    attributes={"count": len(tracks)},
                )
            )
            for zone in self.zones:
                events.append(
                    EventSpec(
                        type="occupancy",
                        zone_id=zone.id,
                        track_id=None,
                        ts_start=ts,
                        attributes={"count": self._zone_count(zone.id)},
                    )
                )
        if self._last_queue is None or (ts - self._last_queue).total_seconds() >= QUEUE_PERIOD_S:
            self._last_queue = ts
            for zone in self.zones:
                if zone.kind != "checkout_area":
                    continue
                events.append(
                    EventSpec(
                        type="queue_len",
                        zone_id=zone.id,
                        track_id=None,
                        ts_start=ts,
                        attributes={"queue_len": self._zone_count(zone.id)},
                    )
                )
        return events
