import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from worker.zone_engine import EventSpec

log = logging.getLogger(__name__)

METRIC_ATTR = {"queue_len": "queue_len", "occupancy": "count"}


@dataclass
class RuleRuntime:
    id: uuid.UUID
    zone_id: uuid.UUID
    zone_name: str
    metric: str
    threshold: int
    sustain_seconds: int


@dataclass
class FiredAlert:
    rule_id: uuid.UUID
    zone_name: str
    metric: str
    value: int
    triggered_at: datetime
    message: str


class AlertEngine:
    def __init__(self):
        self.rules: list[RuleRuntime] = []
        self.state: dict[uuid.UUID, dict] = {}

    def update_rules(self, rule_rows) -> None:
        self.rules = [
            RuleRuntime(
                id=r.id,
                zone_id=r.zone_id,
                zone_name=name,
                metric=r.metric,
                threshold=r.threshold,
                sustain_seconds=r.sustain_seconds,
            )
            for r, name in rule_rows
        ]

    def process(self, event: EventSpec) -> list[FiredAlert]:
        if event.type not in METRIC_ATTR:
            return []
        fired: list[FiredAlert] = []
        for rule in self.rules:
            if rule.metric != event.type or rule.zone_id != event.zone_id:
                continue
            value = event.attributes.get(METRIC_ATTR[rule.metric], 0)
            st = self.state.setdefault(rule.id, {"breach_since": None, "fired": False})
            if value >= rule.threshold:
                if st["breach_since"] is None:
                    st["breach_since"] = event.ts_start
                sustained = (event.ts_start - st["breach_since"]).total_seconds()
                if sustained >= rule.sustain_seconds and not st["fired"]:
                    st["fired"] = True
                    fired.append(
                        FiredAlert(
                            rule_id=rule.id,
                            zone_name=rule.zone_name,
                            metric=rule.metric,
                            value=value,
                            triggered_at=event.ts_start,
                            message=(
                                f"{rule.metric} in '{rule.zone_name}' is {value} "
                                f"(threshold {rule.threshold}) for over {rule.sustain_seconds}s"
                            ),
                        )
                    )
            else:
                st["breach_since"] = None
                st["fired"] = False
        return fired
