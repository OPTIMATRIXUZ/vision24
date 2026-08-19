import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from worker.alert_engine import AlertEngine
from worker.zone_engine import EventSpec

pytestmark = pytest.mark.unit

T0 = datetime(2026, 7, 30, 12, 0, 0)
ZONE = uuid.uuid4()


@dataclass
class RuleRow:
    id: uuid.UUID
    zone_id: uuid.UUID
    metric: str
    threshold: int
    sustain_seconds: int


def engine_with(metric="queue_len", threshold=3, sustain=15, zone_id=ZONE) -> AlertEngine:
    eng = AlertEngine()
    rule = RuleRow(
        id=uuid.uuid4(),
        zone_id=zone_id,
        metric=metric,
        threshold=threshold,
        sustain_seconds=sustain,
    )
    eng.update_rules([(rule, "Kassa")])
    return eng


def sample(value: int, seconds: float, metric="queue_len", zone_id=ZONE) -> EventSpec:
    attr = "queue_len" if metric == "queue_len" else "count"
    return EventSpec(
        type=metric,
        zone_id=zone_id,
        track_id=None,
        ts_start=T0 + timedelta(seconds=seconds),
        attributes={attr: value},
    )


def test_breach_must_be_sustained_before_it_fires():
    eng = engine_with(threshold=3, sustain=15)
    assert eng.process(sample(5, 0)) == []
    assert eng.process(sample(5, 14)) == []
    fired = eng.process(sample(5, 15))
    assert len(fired) == 1
    assert fired[0].value == 5


def test_it_fires_once_not_on_every_sample():
    eng = engine_with(sustain=15)
    eng.process(sample(5, 0))
    assert len(eng.process(sample(5, 15))) == 1
    assert eng.process(sample(5, 20)) == []
    assert eng.process(sample(9, 40)) == []


def test_dropping_below_threshold_rearms_the_rule():
    eng = engine_with(threshold=3, sustain=15)
    eng.process(sample(5, 0))
    assert len(eng.process(sample(5, 15))) == 1

    eng.process(sample(1, 20))

    eng.process(sample(5, 25))
    assert eng.process(sample(5, 39)) == []
    assert len(eng.process(sample(5, 40))) == 1


def test_a_momentary_spike_never_fires():
    eng = engine_with(threshold=3, sustain=15)
    for t, v in [(0, 5), (2, 5), (4, 1), (6, 5), (8, 1)]:
        assert eng.process(sample(v, t)) == []


def test_value_exactly_at_threshold_counts_as_a_breach():
    eng = engine_with(threshold=3, sustain=0)
    assert len(eng.process(sample(3, 0))) == 1


def test_events_for_other_zones_are_ignored():
    eng = engine_with(sustain=0)
    assert eng.process(sample(9, 0, zone_id=uuid.uuid4())) == []


def test_events_of_the_wrong_metric_are_ignored():
    eng = engine_with(metric="queue_len", sustain=0)
    assert eng.process(sample(9, 0, metric="occupancy")) == []


def test_non_metric_events_are_ignored():
    eng = engine_with(sustain=0)
    entry = EventSpec(type="entry", zone_id=ZONE, track_id=1, ts_start=T0)
    assert eng.process(entry) == []


def test_occupancy_rules_read_the_count_attribute():
    eng = engine_with(metric="occupancy", threshold=2, sustain=0)
    fired = eng.process(sample(4, 0, metric="occupancy"))
    assert len(fired) == 1
    assert fired[0].value == 4


def test_message_names_the_zone_and_the_threshold():
    eng = engine_with(threshold=3, sustain=15)
    eng.process(sample(5, 0))
    msg = eng.process(sample(5, 15))[0].message
    assert "Kassa" in msg and "5" in msg and "3" in msg
