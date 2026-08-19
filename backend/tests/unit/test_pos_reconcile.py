from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.errors import ValidationError
from app.services.pos import (
    _assign_sales_to_visits,
    _Interval,
    _month_bounds,
    _visits_without_a_sale,
    _ZonePresence,
)

UTC = ZoneInfo("UTC")
T0 = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def _presence(*offsets_s: float) -> _ZonePresence:
    p = _ZonePresence(zone=None)
    p.occupied_ts = [T0 + timedelta(seconds=s) for s in offsets_s]
    p.sample_ts = list(p.occupied_ts)
    return p


def _covered(occupied_s: list[float], empty_s: list[float]) -> _ZonePresence:
    p = _ZonePresence(zone=None)
    p.occupied_ts = sorted(T0 + timedelta(seconds=s) for s in occupied_s)
    p.sample_ts = sorted(T0 + timedelta(seconds=s) for s in occupied_s + empty_s)
    return p


class TestOccupiedNear:
    def test_hit_inside_window(self):
        assert _presence(0).occupied_near(T0 + timedelta(seconds=10), window_s=30)

    def test_boundary_is_inclusive(self):
        assert _presence(0).occupied_near(T0 + timedelta(seconds=30), window_s=30)
        assert _presence(0).occupied_near(T0 - timedelta(seconds=30), window_s=30)

    def test_miss_outside_window(self):
        assert not _presence(0).occupied_near(T0 + timedelta(seconds=31), window_s=30)

    def test_empty_timeline_never_matches(self):
        assert not _presence().occupied_near(T0, window_s=30)

    def test_nearest_neighbor_on_both_sides(self):
        p = _presence(0, 120)
        assert p.occupied_near(T0 + timedelta(seconds=100), window_s=30)
        assert not p.occupied_near(T0 + timedelta(seconds=60), window_s=30)


class TestCoveredNear:

    def test_empty_sample_is_coverage(self):
        p = _covered(occupied_s=[], empty_s=[0])
        assert p.covered_near(T0 + timedelta(seconds=10), window_s=30)
        assert not p.occupied_near(T0 + timedelta(seconds=10), window_s=30)

    def test_no_samples_at_all_is_not_coverage(self):
        assert not _covered([], []).covered_near(T0, window_s=30)

    def test_coverage_respects_the_window(self):
        p = _covered(occupied_s=[], empty_s=[0])
        assert p.covered_near(T0 + timedelta(seconds=30), window_s=30)
        assert not p.covered_near(T0 + timedelta(seconds=31), window_s=30)

    def test_gap_between_analyzed_clips_is_not_coverage(self):
        p = _covered(occupied_s=[0, 600], empty_s=[])
        assert not p.covered_near(T0 + timedelta(seconds=300), window_s=30)

    def test_occupied_implies_covered(self):
        p = _presence(0)
        assert p.covered_near(T0, window_s=30)


class TestEmptyNear:
    def test_requires_both_covered_and_unoccupied(self):
        covered_and_empty = _covered(occupied_s=[], empty_s=[0])
        assert covered_and_empty.empty_near(T0, window_s=30)

        occupied = _presence(0)
        assert not occupied.empty_near(T0, window_s=30)

        blind = _covered([], [])
        assert not blind.empty_near(T0, window_s=30)

    def test_empty_sample_next_to_an_occupied_one_is_not_empty(self):
        p = _covered(occupied_s=[20], empty_s=[0])
        assert not p.empty_near(T0, window_s=30)


class _Zone:
    def __init__(self, name="Z1"):
        self.id = name
        self.name = name


class _Sale:
    def __init__(self, offset_s: float, zone_id=None):
        self.ts = T0 + timedelta(seconds=offset_s)
        self.zone_id = zone_id


def _visit(zone: _Zone, start_s: float, end_s: float):
    return (
        zone,
        _Interval(start=T0 + timedelta(seconds=start_s), end=T0 + timedelta(seconds=end_s)),
    )


def _bare(visits, sales, window_s=30):
    return [
        f"{int((iv.start - T0).total_seconds())}-{int((iv.end - T0).total_seconds())}"
        for _z, iv in _visits_without_a_sale(visits, sales, window_s)
    ]


class TestVisitsWithoutASale:

    def test_one_sale_cannot_cover_two_visits(self):
        z = _Zone()
        visits = [_visit(z, 0, 20), _visit(z, 40, 60)]
        assert len(_bare(visits, [_Sale(50)])) == 1

    def test_each_visit_with_its_own_sale_is_clean(self):
        z = _Zone()
        visits = [_visit(z, 0, 20), _visit(z, 40, 60)]
        assert _bare(visits, [_Sale(10), _Sale(50)]) == []

    def test_three_visits_two_sales_leaves_exactly_one(self):
        z = _Zone()
        visits = [_visit(z, 0, 20), _visit(z, 40, 60), _visit(z, 80, 100)]
        assert len(_bare(visits, [_Sale(10), _Sale(50)])) == 1

    def test_sale_outside_every_window_covers_nothing(self):
        z = _Zone()
        visits = [_visit(z, 0, 20)]
        assert len(_bare(visits, [_Sale(200)])) == 1

    def test_sale_pinned_to_another_register_does_not_count(self):
        a, b = _Zone("A"), _Zone("B")
        visits = [_visit(a, 0, 20)]
        assert len(_bare(visits, [_Sale(10, zone_id=b.id)])) == 1
        assert _bare(visits, [_Sale(10, zone_id=a.id)]) == []

    def test_unpinned_sale_serves_any_register(self):
        visits = [_visit(_Zone("A"), 0, 20)]
        assert _bare(visits, [_Sale(10)]) == []

    def test_matching_is_maximum_when_one_pool_serves_all(self):
        z = _Zone()
        visits = [_visit(z, 0, 10), _visit(z, 20, 30)]
        assert _bare(visits, [_Sale(5), _Sale(25)]) == []

    def test_no_visits_means_nothing_to_report(self):
        assert _bare([], [_Sale(10)]) == []


class TestAssignSalesToVisits:

    def test_each_visit_gets_its_own_sale(self):
        z = _Zone()
        visits = [_visit(z, 0, 20), _visit(z, 40, 60)]
        assert _assign_sales_to_visits(visits, [_Sale(10), _Sale(50)], 30) == {0: 0, 1: 1}

    def test_unmatched_visit_maps_to_none(self):
        z = _Zone()
        visits = [_visit(z, 0, 20), _visit(z, 40, 60)]
        a = _assign_sales_to_visits(visits, [_Sale(50)], 30)
        assert sorted(a) == [0, 1]
        assert sum(1 for s in a.values() if s is None) == 1

    def test_bare_wrapper_is_exactly_the_unassigned_set(self):
        z = _Zone()
        visits = [_visit(z, 0, 20), _visit(z, 40, 60), _visit(z, 80, 100)]
        sales = [_Sale(10), _Sale(50)]
        a = _assign_sales_to_visits(visits, sales, 30)
        bare = _visits_without_a_sale(visits, sales, 30)
        assert [visits.index(v) for v in bare] == sorted(i for i, s in a.items() if s is None)


class TestInterval:
    def test_duration(self):
        iv = _Interval(start=T0, end=T0 + timedelta(seconds=24))
        assert iv.duration_s == 24.0


class TestMonthBounds:
    class _Site:
        timezone = "Asia/Tashkent"

    def test_explicit_month(self):
        month, start, end = _month_bounds(self._Site(), "2026-08")
        assert month == "2026-08"
        assert (start.year, start.month, start.day) == (2026, 8, 1)
        assert (end.year, end.month) == (2026, 9)

    def test_december_rolls_into_next_year(self):
        _, _start, end = _month_bounds(self._Site(), "2026-12")
        assert (end.year, end.month) == (2027, 1)

    @pytest.mark.parametrize("bad", ["2026-13", "2026-00", "garbage", "2026-8", "2026/08", ""])
    def test_malformed_month_is_rejected(self, bad):
        with pytest.raises(ValidationError):
            _month_bounds(self._Site(), bad)
