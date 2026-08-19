import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from worker.types import TrackedPerson
from worker.zone_engine import HYSTERESIS_S, MIN_TRACK_AGE_S, STALE_TRACK_S, ZoneEngine

pytestmark = pytest.mark.unit

T0 = datetime(2026, 7, 30, 12, 0, 0)
FRAME_W = FRAME_H = 100

LEFT_HALF = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
FULL_FRAME = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


@dataclass
class ZoneRow:

    id: uuid.UUID
    name: str
    kind: str
    record_clips: bool
    polygon: list


def zone_row(kind: str = "entrance", polygon=None, record_clips: bool = False) -> ZoneRow:
    return ZoneRow(
        id=uuid.uuid4(),
        name=f"{kind}-zone",
        kind=kind,
        record_clips=record_clips,
        polygon=polygon if polygon is not None else LEFT_HALF,
    )


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def person(track_id: int = 1, *, inside: bool) -> TrackedPerson:
    x = 20.0 if inside else 80.0
    return TrackedPerson(track_id=track_id, xyxy=(x - 5, 40.0, x + 5, 80.0), conf=0.9)


def types_of(events, kind: str):
    return [e for e in events if e.type == kind]


def engine_with(*rows) -> ZoneEngine:
    eng = ZoneEngine()
    eng.update_zones(list(rows))
    return eng


class TestEntry:
    def test_entry_requires_both_maturity_and_hysteresis(self):
        eng = engine_with(zone_row())

        assert types_of(eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H), "entry") == []

        mature_at = MIN_TRACK_AGE_S
        assert (
            types_of(eng.process([person(inside=True)], at(mature_at), FRAME_W, FRAME_H), "entry")
            == []
        )

        events = eng.process([person(inside=True)], at(mature_at + HYSTERESIS_S), FRAME_W, FRAME_H)
        entries = types_of(events, "entry")
        assert len(entries) == 1
        assert entries[0].track_id == 1

    def test_flicker_never_becomes_a_visitor(self):
        eng = engine_with(zone_row())
        for t in (0.0, 0.2, 0.4, 0.6):
            assert (
                types_of(eng.process([person(inside=True)], at(t), FRAME_W, FRAME_H), "entry") == []
            )

    def test_entry_carries_the_record_clip_flag(self):
        eng = engine_with(zone_row(kind="store_room", record_clips=True))
        eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S), FRAME_W, FRAME_H)
        events = eng.process(
            [person(inside=True)], at(MIN_TRACK_AGE_S + HYSTERESIS_S), FRAME_W, FRAME_H
        )
        assert types_of(events, "entry")[0].record_clip is True

    def test_a_frame_on_the_current_side_resets_the_hysteresis_timer(self):
        eng = engine_with(zone_row())
        eng.process([person(inside=False)], at(0), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(1.0), FRAME_W, FRAME_H)
        eng.process([person(inside=False)], at(1.2), FRAME_W, FRAME_H)
        events = eng.process([person(inside=True)], at(1.4), FRAME_W, FRAME_H)
        assert types_of(events, "entry") == []


class TestExitAndDwell:
    def _enter(self, eng) -> float:
        eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S), FRAME_W, FRAME_H)
        entered = MIN_TRACK_AGE_S + HYSTERESIS_S
        assert types_of(eng.process([person(inside=True)], at(entered), FRAME_W, FRAME_H), "entry")
        return entered

    def test_exit_emits_exit_and_dwell_with_correct_duration(self):
        eng = engine_with(zone_row())
        entered = self._enter(eng)

        eng.process([person(inside=False)], at(entered + 0.2), FRAME_W, FRAME_H)
        left = entered + 0.2 + HYSTERESIS_S
        events = eng.process([person(inside=False)], at(left), FRAME_W, FRAME_H)

        assert len(types_of(events, "exit")) == 1
        dwell = types_of(events, "dwell")
        assert len(dwell) == 1
        assert dwell[0].attributes["dwell_s"] == pytest.approx(left - entered, abs=0.05)
        assert dwell[0].ts_start == at(entered)
        assert dwell[0].ts_end == at(left)
        assert "truncated" not in dwell[0].attributes

    def test_reentry_counts_as_two_visits(self):
        eng = engine_with(zone_row())
        entered = self._enter(eng)
        eng.process([person(inside=False)], at(entered + 0.2), FRAME_W, FRAME_H)
        eng.process([person(inside=False)], at(entered + 0.2 + HYSTERESIS_S), FRAME_W, FRAME_H)

        back = entered + 2.0
        eng.process([person(inside=True)], at(back), FRAME_W, FRAME_H)
        events = eng.process([person(inside=True)], at(back + HYSTERESIS_S), FRAME_W, FRAME_H)
        assert len(types_of(events, "entry")) == 1


class TestStaleTracks:
    def test_vanished_track_is_closed_out_and_its_state_pruned(self):
        eng = engine_with(zone_row())
        eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S), FRAME_W, FRAME_H)
        entered = MIN_TRACK_AGE_S + HYSTERESIS_S
        eng.process([person(inside=True)], at(entered), FRAME_W, FRAME_H)

        gone_at = entered + STALE_TRACK_S + 0.1
        events = eng.process([], at(gone_at), FRAME_W, FRAME_H)

        assert len(types_of(events, "exit")) == 1
        assert types_of(events, "dwell")[0].attributes["dwell_s"] == pytest.approx(
            gone_at - entered, abs=0.05
        )
        assert eng.state == {}
        assert eng.track_first_seen == {}
        assert eng.track_last_seen == {}


class TestFlush:
    def test_flush_closes_open_visits_and_marks_them_truncated(self):
        eng = engine_with(zone_row())
        eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S + HYSTERESIS_S), FRAME_W, FRAME_H)

        events = eng.flush(at(10.0))
        dwell = types_of(events, "dwell")
        assert len(dwell) == 1
        assert dwell[0].attributes["truncated"] is True
        assert eng.state == {}

    def test_flush_is_silent_when_nobody_is_inside(self):
        eng = engine_with(zone_row())
        eng.process([person(inside=False)], at(0), FRAME_W, FRAME_H)
        assert eng.flush(at(5.0)) == []


class TestPeriodicSamples:
    def test_occupancy_is_sampled_globally_and_per_zone(self):
        zone = zone_row()
        eng = engine_with(zone)
        events = eng.process([], at(0), FRAME_W, FRAME_H)
        occ = types_of(events, "occupancy")
        assert {e.zone_id for e in occ} == {None, zone.id}

    def test_occupancy_respects_its_period(self):
        eng = engine_with(zone_row())
        assert types_of(eng.process([], at(0), FRAME_W, FRAME_H), "occupancy")
        assert types_of(eng.process([], at(0.5), FRAME_W, FRAME_H), "occupancy") == []
        assert types_of(eng.process([], at(1.0), FRAME_W, FRAME_H), "occupancy")

    def test_queue_length_is_only_sampled_for_checkout_zones(self):
        eng = engine_with(zone_row(kind="entrance"))
        assert types_of(eng.process([], at(0), FRAME_W, FRAME_H), "queue_len") == []

        checkout = zone_row(kind="checkout_area")
        eng2 = engine_with(checkout)
        queue = types_of(eng2.process([], at(0), FRAME_W, FRAME_H), "queue_len")
        assert len(queue) == 1
        assert queue[0].zone_id == checkout.id

    def test_queue_length_respects_its_longer_period(self):
        eng = engine_with(zone_row(kind="checkout_area"))
        assert types_of(eng.process([], at(0), FRAME_W, FRAME_H), "queue_len")
        assert types_of(eng.process([], at(1.5), FRAME_W, FRAME_H), "queue_len") == []
        assert types_of(eng.process([], at(2.0), FRAME_W, FRAME_H), "queue_len")

    def test_occupancy_counts_only_mature_tracks(self):
        eng = engine_with(zone_row())
        events = eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H)
        assert types_of(events, "occupancy")[0].attributes["count"] == 0

        events = eng.process([person(inside=True)], at(MIN_TRACK_AGE_S + 1.0), FRAME_W, FRAME_H)
        assert types_of(events, "occupancy")[0].attributes["count"] == 1


class TestZoneManagement:
    def test_a_bad_polygon_is_skipped_without_killing_the_others(self):
        good = zone_row(polygon=FULL_FRAME)
        bad = zone_row(polygon=[[0.0, 0.0]])
        eng = engine_with(bad, good)
        assert [z.id for z in eng.zones] == [good.id]

    def test_removing_a_zone_drops_its_track_state(self):
        zone = zone_row()
        eng = engine_with(zone)
        eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S + HYSTERESIS_S), FRAME_W, FRAME_H)
        assert eng.state

        eng.update_zones([])
        assert eng.state == {}

    def test_zone_counts_reports_confirmed_occupants(self):
        zone = zone_row()
        eng = engine_with(zone)
        assert eng.zone_counts() == {zone.id: 0}
        eng.process([person(inside=True)], at(0), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S), FRAME_W, FRAME_H)
        eng.process([person(inside=True)], at(MIN_TRACK_AGE_S + HYSTERESIS_S), FRAME_W, FRAME_H)
        assert eng.zone_counts() == {zone.id: 1}
