from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from worker.delivery import (
    ProductIndex,
    ProductRef,
    Trip,
    TripResult,
    TripSegmenter,
    _accept_package,
    _aggregate,
    _apply_vlm_verdict,
    _Keyframe,
    _select_candidates,
    _verify_with_vlm,
    classify,
)
from worker.types import DetectedObject, TrackedPerson

TRUCK = SimpleNamespace(id="truck-zone", polygon=[[0.0, 0.0], [0.3, 0.0], [0.3, 1.0], [0.0, 1.0]])
DOOR = SimpleNamespace(id="door-zone", polygon=[[0.7, 0.0], [1.0, 0.0], [1.0, 1.0], [0.7, 1.0]])

BASE = datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
FPS = 10


def make_segmenter() -> TripSegmenter:
    return TripSegmenter([TRUCK], [DOOR])


def track_at(track_id: int, x: float, y: float = 0.5) -> TrackedPerson:
    px, py = x * 100, y * 100
    return TrackedPerson(track_id=track_id, xyxy=(px - 5, py - 30, px + 5, py), conf=0.9)


def run_path(seg: TripSegmenter, path: list[tuple[float, float, float]], track_id: int = 1):
    done = []
    for t, x, y in path:
        ts = BASE + timedelta(seconds=t)
        frame_idx = int(t * FPS)
        done.extend(seg.observe([track_at(track_id, x, y)], ts, frame_idx, 100, 100))
    return done


def run_multi(seg: TripSegmenter, samples: list[tuple[float, int | None, float, float]]):
    done = []
    for t, tid, x, y in samples:
        ts = BASE + timedelta(seconds=t)
        tracks = [] if tid is None else [track_at(tid, x, y)]
        done.extend(seg.observe(tracks, ts, int(t * FPS), 100, 100))
    return done


def steps(t0: float, t1: float, x: float, y: float = 0.5) -> list[tuple[float, float, float]]:
    return [(t / FPS, x, y) for t in range(int(t0 * FPS), int(t1 * FPS))]


class TestTripSegmenter:
    def test_truck_to_door_is_one_complete_trip(self):
        seg = make_segmenter()
        trips = run_path(seg, steps(0, 2, 0.15) + steps(2, 4, 0.5) + steps(4, 6, 0.85))
        assert len(trips) == 1
        trip = trips[0]
        assert trip.complete
        assert trip.track_id == 1
        assert abs((trip.t_start - BASE).total_seconds() - 2.0) < 0.3
        assert trip.t_end is not None
        assert trip.candidates, "mid-carry keyframe candidates were collected"
        assert seg.completed_count == 1
        assert seg.incomplete_count == 0

    def test_two_trips_require_returning_to_the_truck(self):
        seg = make_segmenter()
        trips = run_path(
            seg,
            steps(0, 2, 0.15)
            + steps(2, 4, 0.5)
            + steps(4, 6, 0.85)
            + steps(6, 8, 0.5)
            + steps(8, 10, 0.15)
            + steps(10, 12, 0.5)
            + steps(12, 14, 0.85),
        )
        assert len(trips) == 2
        assert seg.completed_count == 2

    def test_return_leg_alone_is_not_a_trip(self):
        seg = make_segmenter()
        trips = run_path(seg, steps(0, 2, 0.85) + steps(2, 4, 0.5) + steps(4, 6, 0.15))
        assert trips == []
        assert seg.completed_count == 0

    def test_customer_track_is_ignored(self):
        seg = make_segmenter()
        trips = run_path(seg, steps(0, 2, 0.5) + steps(2, 4, 0.85))
        assert trips == []
        assert seg.completed_count == 0
        assert seg.incomplete_count == 0

    def test_going_back_to_truck_drops_the_trip_silently(self):
        seg = make_segmenter()
        trips = run_path(seg, steps(0, 2, 0.15) + steps(2, 4, 0.5) + steps(4, 6, 0.15))
        assert trips == []
        assert seg.completed_count == 0
        assert seg.incomplete_count == 0

    def test_vanishing_at_the_door_completes_the_trip(self):
        seg = make_segmenter()
        trips = run_path(seg, steps(0, 2, 0.15) + steps(2, 4, 0.5) + steps(4, 5, 0.68))
        trips += seg.flush()
        assert len(trips) == 1
        assert trips[0].complete

    def test_vanishing_mid_floor_is_incomplete(self):
        seg = make_segmenter()
        trips = run_path(seg, steps(0, 2, 0.15) + steps(2, 4, 0.5))
        trips += seg.flush()
        assert trips == []
        assert seg.incomplete_count == 1

    def test_overlong_trip_is_abandoned(self):
        seg = make_segmenter()
        path = steps(0, 2, 0.15) + steps(2, 95, 0.5) + steps(95, 97, 0.85)
        trips = run_path(seg, path)
        assert trips == []
        assert seg.incomplete_count == 1

    def test_candidates_avoid_the_truck_edge_but_keep_the_door_approach(self):
        seg = make_segmenter()
        trips = run_path(seg, steps(0, 2, 0.15) + steps(2, 4, 0.5) + steps(4, 6, 0.85))
        xs = {x for _idx, xyxy, _a in trips[0].candidates for x in [(xyxy[0] + xyxy[2]) / 200]}
        assert all(x >= 0.33 for x in xs), "no frames at the truck edge"
        assert any(x > 0.7 for x in xs), "the door approach is collected"


class TestStitching:

    def multi_steps(self, t0, t1, tid, x, y=0.5):
        return [(t / FPS, tid, x, y) for t in range(int(t0 * FPS), int(t1 * FPS))]

    def gap(self, t0, t1):
        return [(t / FPS, None, 0.0, 0.0) for t in range(int(t0 * FPS), int(t1 * FPS))]

    def test_id_switch_mid_carry_is_stitched(self):
        seg = make_segmenter()
        trips = run_multi(
            seg,
            self.multi_steps(0, 2, 1, 0.15)
            + self.multi_steps(2, 4, 1, 0.45)
            + self.gap(4, 5)
            + self.multi_steps(5, 8, 2, 0.55)
            + self.multi_steps(8, 10, 2, 0.85),
        )
        assert len(trips) == 1
        assert trips[0].complete
        assert trips[0].stitched
        assert trips[0].track_id == 2
        assert seg.incomplete_count == 0
        assert trips[0].candidates

    def test_id_switch_at_the_truck_is_stitched(self):
        seg = make_segmenter()
        trips = run_multi(
            seg,
            self.multi_steps(0, 3, 1, 0.15)
            + self.gap(3, 4)
            + self.multi_steps(4, 6, 2, 0.35)
            + self.multi_steps(6, 8, 2, 0.85),
        )
        assert len(trips) == 1
        assert trips[0].complete
        assert trips[0].stitched
        assert seg.completed_count == 1
        assert seg.incomplete_count == 0

    def test_distant_newcomer_is_not_a_successor(self):
        seg = make_segmenter()
        trips = run_multi(
            seg,
            self.multi_steps(0, 2, 1, 0.15)
            + self.multi_steps(2, 4, 1, 0.45)
            + self.gap(4, 5)
            + self.multi_steps(5, 6, 2, 0.95, 0.2)
            + self.multi_steps(6, 8, 2, 0.85),
        )
        assert trips == []
        assert seg.completed_count == 0
        assert seg.incomplete_count == 1

    def test_late_newcomer_is_not_a_successor(self):
        seg = make_segmenter()
        trips = run_multi(
            seg,
            self.multi_steps(0, 2, 1, 0.15)
            + self.multi_steps(2, 4, 1, 0.45)
            + self.gap(4, 8)
            + self.multi_steps(8, 10, 2, 0.5)
            + self.multi_steps(10, 12, 2, 0.85),
        )
        assert trips == []
        assert seg.incomplete_count == 1


def _index(threshold_products: int = 2) -> ProductIndex:
    products = [
        ProductRef(
            id="a", name="Cola crate", units_per_package=24, unit_label="bottles", images=[]
        ),
        ProductRef(id="b", name="Chips box", units_per_package=None, unit_label=None, images=[]),
    ][:threshold_products]
    vecs = np.eye(len(products), 512, dtype=np.float32)
    return ProductIndex(products=products, vecs=vecs, owner=np.arange(len(products)))


def _vec(a: float, b: float) -> np.ndarray:
    v = np.zeros(512, dtype=np.float32)
    v[0], v[1] = a, b
    rest = 1.0 - a * a - b * b
    if rest > 0:
        v[2] = np.sqrt(rest)
    return v


class TestClassify:
    def test_confident_match(self):
        verdicts = classify(_index(), np.stack([_vec(0.8, 0.1)]))
        pidx, sim, margin = verdicts[0]
        assert pidx == 0
        assert sim == pytest.approx(0.8, abs=1e-5)
        assert margin == pytest.approx(0.7, abs=1e-5)

    def test_below_threshold_is_unknown(self):
        verdicts = classify(_index(), np.stack([_vec(0.44, 0.1)]))
        assert verdicts[0][0] is None

    def test_margin_reported_for_ambiguity(self):
        verdicts = classify(_index(), np.stack([_vec(0.71, 0.70)]))
        pidx, _sim, margin = verdicts[0]
        assert pidx == 0
        assert margin < 0.04


def _kf(frame_idx: int, slots: list[int]) -> _Keyframe:
    kf = _Keyframe(frame_idx=frame_idx, person_xyxy=(0, 0, 40, 100))
    for slot in slots:
        kf.packages.append(DetectedObject(xyxy=(10, 30, 60, 80), conf=0.5, class_name="box"))
        kf.crop_slots.append(slot)
    return kf


def _trip(complete: bool = True) -> Trip:
    return Trip(track_id=1, t_start=BASE, frame_start=0, complete=complete, t_end=BASE)


class TestAcceptPackage:

    PERSON = (800, 500, 1000, 900)

    def _det(self, cx, cy, w=140, h=120):
        return DetectedObject(
            xyxy=(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), conf=0.1, class_name="box"
        )

    def test_carried_box_is_accepted(self):
        assert _accept_package(self._det(900, 680), self.PERSON, 1920, 1080)

    def test_static_background_box_is_rejected(self):
        assert not _accept_package(self._det(1120, 680), self.PERSON, 1920, 1080)

    def test_box_at_feet_is_rejected(self):
        assert not _accept_package(self._det(900, 895), self.PERSON, 1920, 1080)

    def test_tiny_noise_is_rejected(self):
        assert not _accept_package(self._det(900, 680, w=40, h=40), self.PERSON, 1920, 1080)


class TestSelectCandidates:
    def test_recency_wins_over_area(self):
        trip = Trip(track_id=1, t_start=BASE, frame_start=0)
        for f in range(0, 300, 10):
            trip.candidates.append((f, (0, 0, 400, 800), 320_000.0))
        for f in range(300, 360, 10):
            trip.candidates.append((f, (0, 0, 100, 200), 20_000.0))
        picked_frames = [f for f, _ in _select_candidates(trip, stride=3)]
        assert picked_frames == [310, 320, 330, 340, 350]


class _FakeProvider:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def generate(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(text=self.text)


def _uncertain_result(jpeg=b"jpg") -> TripResult:
    result = TripResult(trip=_trip())
    kf = _Keyframe(frame_idx=0, person_xyxy=(0, 0, 40, 100))
    kf.vlm_jpeg = jpeg
    result.keyframes = [kf]
    result.count_total = 1
    result.unmatched = 1
    result.count_unstable = True
    return result


def _index_with_images() -> ProductIndex:
    index = _index()
    for product in index.products:
        product.images = [np.full((60, 60, 3), 127, dtype=np.uint8)]
    return index


class TestBatchedVlm:

    def test_one_call_verifies_every_trip(self, monkeypatch):
        import app.services.ai.provider as provider_mod

        results = [_uncertain_result(), _uncertain_result()]
        fake = _FakeProvider(
            '[{"trip": 1, "count": 2, "types": ["Cola crate", "Cola crate"]},'
            ' {"trip": 2, "count": 1, "types": ["Chips box"]}]'
        )
        monkeypatch.setattr(provider_mod, "get_provider", lambda role="default": fake)

        _verify_with_vlm(results, _index_with_images())

        assert fake.calls == 1
        assert all(r.verified_by_vlm for r in results)
        assert results[0].count_total == 2
        assert results[0].count_basis == "vlm"
        assert results[0].items[0]["product_name"] == "Cola crate"
        assert results[1].items[0]["product_name"] == "Chips box"

    def test_malformed_answer_changes_nothing(self, monkeypatch):
        import app.services.ai.provider as provider_mod

        result = _uncertain_result()
        fake = _FakeProvider("I could not decide, sorry!")
        monkeypatch.setattr(provider_mod, "get_provider", lambda role="default": fake)

        with pytest.raises(Exception):  # noqa: B017 - caller catches and keeps CLIP results
            _verify_with_vlm([result], _index_with_images())
        assert result.items == []
        assert result.count_total == 1

    def test_vlm_adds_a_stacked_product_when_consistent(self):
        result = TripResult(trip=_trip())
        result.count_total = 1
        result.count_basis = "median_keyframes"
        result.items = [
            {"product_type_id": "a", "product_name": "Cola crate", "count": 1, "confidence": 0.8}
        ]
        _apply_vlm_verdict(result, 2, ["Cola crate", "Chips box"], _index_with_images())
        assert result.count_total == 2
        by_name = {i["product_name"]: i for i in result.items}
        assert by_name["Chips box"]["count"] == 1
        assert by_name["Chips box"]["confidence"] == 0.5
        assert by_name["Cola crate"]["count"] == 1
        assert not result.vlm_disagreement

    def test_vlm_contradiction_is_flagged_not_believed(self):
        result = TripResult(trip=_trip())
        result.count_total = 1
        result.count_basis = "median_keyframes"
        result.items = [
            {"product_type_id": "a", "product_name": "Cola crate", "count": 1, "confidence": 0.8}
        ]
        _apply_vlm_verdict(result, 2, ["Chips box", "Chips box"], _index_with_images())
        assert result.count_total == 1
        assert len(result.items) == 1
        assert result.items[0]["product_name"] == "Cola crate"

    def test_trip_without_frames_is_skipped(self, monkeypatch):
        import app.services.ai.provider as provider_mod

        result = _uncertain_result()
        result.keyframes[0].vlm_jpeg = None
        fake = _FakeProvider("[]")
        monkeypatch.setattr(provider_mod, "get_provider", lambda role="default": fake)

        _verify_with_vlm([result], _index_with_images())
        assert fake.calls == 0


class TestAggregate:
    def test_median_count_and_type_rule(self):
        result = TripResult(trip=_trip())
        result.keyframes = [_kf(0, [0, 1]), _kf(10, [2, 3]), _kf(20, [4])]
        verdicts = [
            (0, 0.8, 0.3),
            (1, 0.75, 0.2),
            (0, 0.82, 0.3),
            (1, 0.71, 0.2),
            (0, 0.79, 0.3),
        ]
        _aggregate(result, _index(), verdicts, {})
        assert result.count_total == 2
        by_name = {i["product_name"]: i for i in result.items}
        assert by_name["Cola crate"]["count"] == 1
        assert by_name["Chips box"]["count"] == 1
        assert result.unmatched == 0
        assert not result.count_unstable

    def test_type_seen_in_one_keyframe_is_not_counted(self):
        result = TripResult(trip=_trip())
        result.keyframes = [_kf(0, [0]), _kf(10, [1]), _kf(20, [2])]
        verdicts = [(0, 0.8, 0.3), (0, 0.8, 0.3), (1, 0.9, 0.4)]
        _aggregate(result, _index(), verdicts, {})
        names = [i["product_name"] for i in result.items]
        assert names == ["Cola crate"]
        assert result.has_unknown

    def test_ambiguous_margin_is_unknown(self):
        result = TripResult(trip=_trip())
        result.keyframes = [_kf(0, [0]), _kf(10, [1])]
        verdicts = [(0, 0.7, 0.01), (0, 0.7, 0.01)]
        _aggregate(result, _index(), verdicts, {})
        assert result.items == []
        assert result.has_unknown
        assert result.unmatched == result.count_total == 1

    def test_single_detected_keyframe_counts_but_is_flagged(self):
        result = TripResult(trip=_trip())
        result.keyframes = [_kf(0, [0, 1, 2]), _kf(10, [])]
        verdicts = [(0, 0.8, 0.3)] * 3
        _aggregate(result, _index(), verdicts, {})
        assert result.count_total == 3
        assert result.count_basis == "single_keyframe"
        assert result.has_unknown

    def test_unstable_counts_flagged_for_vlm(self):
        result = TripResult(trip=_trip())
        result.keyframes = [_kf(0, [0, 1, 2]), _kf(10, [3])]
        verdicts = [(0, 0.8, 0.3)] * 4
        _aggregate(result, _index(), verdicts, {})
        assert result.count_unstable

    def test_stacked_different_products_count_as_two(self):
        result = TripResult(trip=_trip())
        kf1, kf2 = _kf(0, [0]), _kf(10, [3])
        kf1.half_slots = [(1, 2)]
        kf2.half_slots = [(4, 5)]
        result.keyframes = [kf1, kf2]
        verdicts = [
            (0, 0.8, 0.3),
            (0, 0.75, 0.25),
            (1, 0.7, 0.2),
            (0, 0.82, 0.3),
            (0, 0.76, 0.25),
            (1, 0.72, 0.2),
        ]
        _aggregate(result, _index(), verdicts, {})
        assert result.count_total == 2
        assert result.stack_suspect
        by_name = {i["product_name"]: i["count"] for i in result.items}
        assert by_name == {"Cola crate": 1, "Chips box": 1}

    def test_halves_matching_the_same_product_stay_one_package(self):
        result = TripResult(trip=_trip())
        kf1, kf2 = _kf(0, [0]), _kf(10, [3])
        kf1.half_slots = [(1, 2)]
        kf2.half_slots = [(4, 5)]
        result.keyframes = [kf1, kf2]
        verdicts = [
            (0, 0.8, 0.3),
            (0, 0.75, 0.25),
            (0, 0.7, 0.2),
            (0, 0.82, 0.3),
            (0, 0.76, 0.25),
            (0, 0.72, 0.2),
        ]
        _aggregate(result, _index(), verdicts, {})
        assert result.count_total == 1
        assert not result.stack_suspect
        assert [i["count"] for i in result.items] == [1]

    def test_torso_fallback_on_empty_complete_trip(self):
        result = TripResult(trip=_trip(complete=True))
        result.keyframes = [_kf(0, [])]
        _aggregate(result, _index(), [], {id(result): (0, 0.7, 0.2)})
        assert result.count_total == 1
        assert result.count_basis == "torso_fallback"
        assert result.items[0]["product_name"] == "Cola crate"
        assert result.items[0]["confidence"] <= 0.5
