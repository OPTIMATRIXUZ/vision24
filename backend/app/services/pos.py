import random
import uuid
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import NamedTuple

from sqlalchemy import Integer, delete, func, select, text
from sqlalchemy.orm import Session

from app import storage
from app.config import settings
from app.errors import ValidationError
from app.models import Camera, Clip, Event, PosReceipt, Site, Zone
from app.schemas import (
    DiscrepancyOut,
    PosDiscrepanciesOut,
    PosItem,
    PosReceiptIn,
    PosReceiptOut,
    PosSeenItem,
    PosVisitOut,
    SavingsLine,
    SavingsOut,
)
from app.services.analytics import (
    _queue_breaches,
    list_zones,
    site_day_bounds,
    site_tz,
    snapshot_keys_for_events,
)

GAP_MERGE_S = 5
EVIDENCE_WINDOW_S = 180

_CATALOG = [
    ("4780001", "Coca-Cola 1.5L", 15_000),
    ("4780002", "Non (лепёшка)", 4_000),
    ("4780003", "Sut 1L", 12_000),
    ("4780004", "Choy Ahmad 100g", 18_000),
    ("4780005", "Snickers 50g", 8_000),
    ("4780006", "Olma 1kg", 14_000),
    ("4780007", "Guruch 1kg", 22_000),
    ("4780008", "Kir yuvish kukuni 450g", 35_000),
]


def ingest_receipts(
    db: Session, site: Site, receipts: list[PosReceiptIn], source: str = "api"
) -> tuple[int, int]:
    tz = site_tz(site)
    zone_ids = {z.id for z in list_zones(db, site)}
    for r in receipts:
        if r.zone_id is not None and r.zone_id not in zone_ids:
            raise ValidationError(f"Unknown zone_id {r.zone_id} for this site.")

    wanted = [r.external_id for r in receipts]
    existing = set(
        db.scalars(
            select(PosReceipt.external_id).where(
                PosReceipt.site_id == site.id, PosReceipt.external_id.in_(wanted)
            )
        )
    )
    seen: set[str] = set()
    ingested = 0
    for r in receipts:
        if r.external_id in existing or r.external_id in seen:
            continue
        seen.add(r.external_id)
        ts = r.ts if r.ts.tzinfo is not None else r.ts.replace(tzinfo=tz)
        db.add(
            PosReceipt(
                site_id=site.id,
                zone_id=r.zone_id,
                external_id=r.external_id,
                kind=r.kind,
                ts=ts,
                total=r.total or sum(i.qty * i.unit_price for i in r.items),
                items=[i.model_dump() for i in r.items],
                source=source,
            )
        )
        ingested += 1
    db.commit()
    return ingested, len(receipts) - ingested


@dataclass
class _Interval:
    start: datetime
    end: datetime
    peak: int = 1

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


def _any_within(sorted_ts: list[datetime], ts: datetime, window_s: int) -> bool:
    if not sorted_ts:
        return False
    i = bisect_left(sorted_ts, ts)
    for j in (i - 1, i):
        if 0 <= j < len(sorted_ts) and abs((sorted_ts[j] - ts).total_seconds()) <= window_s:
            return True
    return False


@dataclass
class _ZonePresence:
    zone: Zone
    occupied_ts: list[datetime] = field(default_factory=list)
    sample_ts: list[datetime] = field(default_factory=list)
    intervals: list[_Interval] = field(default_factory=list)

    def occupied_near(self, ts: datetime, window_s: int) -> bool:
        return _any_within(self.occupied_ts, ts, window_s)

    def covered_near(self, ts: datetime, window_s: int) -> bool:
        return _any_within(self.sample_ts, ts, window_s)

    def empty_near(self, ts: datetime, window_s: int) -> bool:
        return self.covered_near(ts, window_s) and not self.occupied_near(ts, window_s)


def _zone_presence(db: Session, zone: Zone, ts_from: datetime, ts_to: datetime) -> _ZonePresence:
    rows = db.execute(
        select(Event.ts_start, Event.attributes)
        .where(
            Event.zone_id == zone.id,
            Event.type == "occupancy",
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
        .order_by(Event.ts_start)
    ).all()

    presence = _ZonePresence(zone=zone)
    current: _Interval | None = None
    for r in rows:
        count = r.attributes.get("count", 0)
        presence.sample_ts.append(r.ts_start)
        if count < 1:
            continue
        presence.occupied_ts.append(r.ts_start)
        if current is not None and (r.ts_start - current.end).total_seconds() <= GAP_MERGE_S:
            current.end = r.ts_start
            current.peak = max(current.peak, count)
        else:
            if current is not None and current.duration_s >= settings.pos_min_presence_s:
                presence.intervals.append(current)
            current = _Interval(start=r.ts_start, end=r.ts_start, peak=count)
    if current is not None and current.duration_s >= settings.pos_min_presence_s:
        presence.intervals.append(current)
    return presence


def _assign_sales_to_visits(
    visits: list[tuple[Zone, _Interval]], sales: list[PosReceipt], window_s: int
) -> dict[int, int | None]:
    order = sorted(range(len(visits)), key=lambda i: (visits[i][1].end, visits[i][1].start))
    claimed: set[int] = set()
    assignment: dict[int, int | None] = {}
    for vi in order:
        zone, iv = visits[vi]
        lo = iv.start - timedelta(seconds=window_s)
        hi = iv.end + timedelta(seconds=window_s)
        assignment[vi] = None
        for si, sale in enumerate(sales):
            if sale.ts < lo:
                continue
            if sale.ts > hi:
                break
            if si in claimed or sale.zone_id not in (None, zone.id):
                continue
            claimed.add(si)
            assignment[vi] = si
            break
    return assignment


def _visits_without_a_sale(
    visits: list[tuple[Zone, _Interval]], sales: list[PosReceipt], window_s: int
) -> list[tuple[Zone, _Interval]]:
    assignment = _assign_sales_to_visits(visits, sales, window_s)
    return [visits[i] for i in sorted(i for i, si in assignment.items() if si is None)]


class _Reconciliation(NamedTuple):

    receipts: list[PosReceipt]
    flags: list[DiscrepancyOut]
    flag_by_receipt: dict[uuid.UUID, str]
    unverified: int
    visit_sales: tuple = ()


def _receipt_out(
    r: PosReceipt, zone_names: dict[uuid.UUID, str], flag: str | None = None
) -> PosReceiptOut:
    return PosReceiptOut(
        id=r.id,
        external_id=r.external_id,
        kind=r.kind,
        ts=r.ts,
        total=r.total,
        items=[PosItem(**item) for item in (r.items or [])],
        zone_id=r.zone_id,
        zone_name=zone_names.get(r.zone_id) if r.zone_id else None,
        source=r.source,
        flag=flag,
    )


def _nearest_snapshot_event(
    db: Session,
    site: Site,
    camera_id: uuid.UUID,
    anchor: datetime,
    lo: datetime | None = None,
    hi: datetime | None = None,
) -> int | None:
    lo = lo or anchor - timedelta(seconds=EVIDENCE_WINDOW_S)
    hi = hi or anchor + timedelta(seconds=EVIDENCE_WINDOW_S)
    rows = db.execute(
        select(Event.id, Event.ts_start)
        .join(Clip, Clip.event_id == Event.id)
        .join(Camera, Event.camera_id == Camera.id)
        .where(
            Camera.site_id == site.id,
            Event.camera_id == camera_id,
            Clip.snapshot_key.is_not(None),
            Event.ts_start >= lo,
            Event.ts_start < hi,
        )
    ).all()
    if not rows:
        return None
    return min(rows, key=lambda r: abs((r.ts_start - anchor).total_seconds())).id


def _visit_verdicts(
    db: Session, zones: list[Zone], ts_from: datetime, ts_to: datetime
) -> list[Event]:
    if not zones:
        return []
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.zone_id.in_([z.id for z in zones]),
                Event.type == "checkout_visit",
                Event.ts_start < ts_to,
                Event.ts_start >= ts_from - timedelta(hours=1),
            )
            .order_by(Event.ts_start)
        )
    )


def _overlapping_verdict(verdicts: list[Event], zone_id: uuid.UUID, iv: _Interval) -> Event | None:
    best, best_overlap = None, 0.0
    for e in verdicts:
        if e.zone_id != zone_id:
            continue
        end = e.ts_end or e.ts_start
        overlap = (min(end, iv.end) - max(e.ts_start, iv.start)).total_seconds()
        if overlap > best_overlap:
            best, best_overlap = e, overlap
    return best


def _reconcile(db: Session, site: Site, day: date | None) -> _Reconciliation:
    tz = site_tz(site)
    ts_from, ts_to = site_day_bounds(site, day)
    window = settings.pos_match_window_s
    unverified = 0

    receipts = list(
        db.scalars(
            select(PosReceipt)
            .where(PosReceipt.site_id == site.id, PosReceipt.ts >= ts_from, PosReceipt.ts < ts_to)
            .order_by(PosReceipt.ts)
        )
    )
    zones = [z for z in list_zones(db, site) if z.kind == "checkout_area"]
    zone_names = {z.id: z.name for z in zones}
    if not zones:
        return _Reconciliation(receipts, [], {}, len(receipts))

    presence = {z.id: _zone_presence(db, z, ts_from, ts_to) for z in zones}
    flags: list[DiscrepancyOut] = []
    flag_by_receipt: dict[uuid.UUID, str] = {}
    evidence: dict[int, int | None] = {}

    for r in receipts:
        if r.kind not in ("sale", "void"):
            continue
        candidates = [presence[r.zone_id]] if r.zone_id in presence else list(presence.values())
        if any(p.occupied_near(r.ts, window) for p in candidates):
            continue
        if not any(p.covered_near(r.ts, window) for p in candidates):
            unverified += 1
            continue
        flag = "no_person_at_sale" if r.kind == "sale" else "void_no_customer"
        zone = candidates[0].zone
        local = r.ts.astimezone(tz)
        word = "Sale" if r.kind == "sale" else "Void"
        flags.append(
            DiscrepancyOut(
                flag=flag,
                ts=r.ts,
                zone_name=zone_names.get(zone.id),
                receipt=_receipt_out(r, zone_names, flag),
                explanation=(
                    f"{word} of {r.total:,} UZS at {local:%H:%M:%S} — the camera saw "
                    f"an empty checkout for ±{window}s around that moment."
                ),
            )
        )
        flag_by_receipt[r.id] = flag
        evidence[len(flags) - 1] = _nearest_snapshot_event(db, site, zone.camera_id, r.ts)

    sales = [r for r in receipts if r.kind == "sale"]
    visits = [(z, iv) for z in zones for iv in presence[z.id].intervals]
    assignment = _assign_sales_to_visits(visits, sales, window)
    visit_sales = tuple(
        (z, iv, sales[si] if (si := assignment.get(i)) is not None else None)
        for i, (z, iv) in enumerate(visits)
    )
    verdicts = _visit_verdicts(db, zones, ts_from, ts_to)
    for i, (z, iv) in enumerate(visits):
        if assignment.get(i) is not None:
            continue
        lo = iv.start - timedelta(seconds=window)
        hi = iv.end + timedelta(seconds=window)
        start_l, end_l = iv.start.astimezone(tz), iv.end.astimezone(tz)
        explanation = (
            f"Somebody spent {iv.duration_s:.0f}s at the checkout "
            f"({start_l:%H:%M:%S}–{end_l:%H:%M:%S}) but no sale was registered "
            f"within ±{window}s."
        )
        verdict = _overlapping_verdict(verdicts, z.id, iv)
        status = "open"
        seen_items = None
        if verdict is not None:
            attrs = verdict.attributes
            items = attrs.get("items") or []
            if items:
                seen_items = [PosSeenItem(name=i["name"], qty=i["qty"]) for i in items]
                listed = ", ".join(f"{i['name']} ×{i['qty']}" for i in items)
                explanation += f" The camera saw: {listed}."
            if (
                attrs.get("kind") == "administrative"
                and float(attrs.get("confidence") or 0) >= settings.pos_vlm_clear_confidence
            ):
                status = "cleared"
                note = str(attrs.get("notes") or "").strip()
                explanation += (
                    f" AI review: not a sale — {note}"
                    if note
                    else (" AI review: administrative visit, not a sale.")
                )
        flags.append(
            DiscrepancyOut(
                flag="unscanned_visit",
                status=status,
                ts=iv.start,
                ts_end=iv.end,
                zone_name=z.name,
                receipt=None,
                seen_items=seen_items,
                explanation=explanation,
            )
        )
        if verdict is not None:
            evidence[len(flags) - 1] = verdict.id
        else:
            mid = iv.start + (iv.end - iv.start) / 2
            evidence[len(flags) - 1] = _nearest_snapshot_event(
                db, site, z.camera_id, mid, lo=lo, hi=hi
            )

    snaps = snapshot_keys_for_events(
        db, site, [eid for eid in evidence.values() if eid is not None]
    )
    for idx, eid in evidence.items():
        flags[idx].evidence_event_id = eid
        if eid is not None and eid in snaps:
            flags[idx].snapshot_url = storage.presign_get(snaps[eid])

    flags.sort(key=lambda f: f.ts)
    return _Reconciliation(receipts, flags, flag_by_receipt, unverified, visit_sales)


def get_discrepancies(db: Session, site: Site, day: date | None = None) -> PosDiscrepanciesOut:
    if day is None:
        day = datetime.now(site_tz(site)).date()
    rec = _reconcile(db, site, day)
    return PosDiscrepanciesOut(
        day=day.isoformat(),
        receipts_total=len(rec.receipts),
        discrepancies=rec.flags,
        unverified_receipts=rec.unverified,
    )


def list_receipts(db: Session, site: Site, day: date | None = None) -> list[PosReceiptOut]:
    zone_names = {z.id: z.name for z in list_zones(db, site)}
    rec = _reconcile(db, site, day)
    return [
        _receipt_out(r, zone_names, rec.flag_by_receipt.get(r.id)) for r in reversed(rec.receipts)
    ]


def list_visits(db: Session, site: Site, day: date | None = None) -> list[PosVisitOut]:
    if day is None:
        day = datetime.now(site_tz(site)).date()
    ts_from, ts_to = site_day_bounds(site, day)
    zone_names = {z.id: z.name for z in list_zones(db, site)}
    checkout_zones = [z for z in list_zones(db, site) if z.kind == "checkout_area"]

    rec = _reconcile(db, site, day)
    verdicts = _visit_verdicts(db, checkout_zones, ts_from, ts_to)
    snaps = snapshot_keys_for_events(db, site, [v.id for v in verdicts])

    out: list[PosVisitOut] = []
    for z, iv, sale in rec.visit_sales:
        verdict = _overlapping_verdict(verdicts, z.id, iv)
        attrs = verdict.attributes if verdict is not None else {}
        snapshot_url = None
        if verdict is not None and verdict.id in snaps:
            snapshot_url = storage.presign_get(snaps[verdict.id])
        out.append(
            PosVisitOut(
                ts_start=iv.start,
                ts_end=iv.end,
                zone_name=z.name,
                kind=attrs.get("kind"),
                items=[
                    PosSeenItem(name=i["name"], qty=i["qty"]) for i in (attrs.get("items") or [])
                ],
                confidence=attrs.get("confidence"),
                notes=attrs.get("notes"),
                snapshot_url=snapshot_url,
                receipt=_receipt_out(sale, zone_names) if sale is not None else None,
            )
        )
    out.sort(key=lambda v: v.ts_start)
    return out


def simulate_day(db: Session, site: Site, day: date | None = None) -> dict:
    tz = site_tz(site)
    if day is None:
        day = datetime.now(tz).date()
    ts_from, ts_to = site_day_bounds(site, day)

    zones = [z for z in list_zones(db, site) if z.kind == "checkout_area"]
    if not zones:
        raise ValidationError("Add a checkout_area zone before simulating the POS feed.")

    db.execute(
        delete(PosReceipt).where(
            PosReceipt.site_id == site.id,
            PosReceipt.source == "simulated",
            PosReceipt.ts >= ts_from,
            PosReceipt.ts < ts_to,
        )
    )
    db.commit()

    rng = random.Random(f"{site.id}:{day.isoformat()}")
    counter = 0

    def receipt(ts: datetime, kind: str, zone_id: uuid.UUID) -> PosReceiptIn:
        nonlocal counter
        counter += 1
        items = [
            PosItem(sku=sku, name=name, qty=rng.randint(1, 3), unit_price=price)
            for sku, name, price in rng.sample(_CATALOG, rng.randint(1, 3))
        ]
        return PosReceiptIn(
            external_id=f"SIM-{day:%Y%m%d}-{counter:04d}",
            kind=kind,
            ts=ts,
            total=sum(i.qty * i.unit_price for i in items),
            items=items,
            zone_id=zone_id,
        )

    presence = {z.id: _zone_presence(db, z, ts_from, ts_to) for z in zones}
    intervals = [(z, iv) for z in zones for iv in presence[z.id].intervals]
    intervals.sort(key=lambda pair: pair[1].start)

    out: list[PosReceiptIn] = []
    planted = {"no_person_at_sale": 0, "void_no_customer": 0, "unscanned_visit": 0}

    skip_idx = rng.randrange(len(intervals)) if len(intervals) >= 2 else None
    for idx, (z, iv) in enumerate(intervals):
        if idx == skip_idx:
            planted["unscanned_visit"] += 1
            continue
        mid = iv.start + (iv.end - iv.start) / 2
        jitter_s = min(10.0, iv.duration_s / 4)
        ts = mid + timedelta(seconds=rng.uniform(-jitter_s, jitter_s))
        out.append(receipt(ts, "sale", z.id))

    z0 = zones[0]
    pres = presence[z0.id]
    empty = [t for t in pres.sample_ts if not pres.occupied_near(t, settings.pos_match_window_s)]
    if empty:
        anchor = empty[len(empty) // 3]
        void_ts = empty[2 * len(empty) // 3]
        if void_ts == anchor and len(empty) > 1:
            void_ts = empty[-1]
        out.append(receipt(anchor, "sale", z0.id))
        planted["no_person_at_sale"] += 1
        if void_ts != anchor:
            out.append(receipt(void_ts, "void", z0.id))
            planted["void_no_customer"] += 1

    ingested, _ = ingest_receipts(db, site, out, source="simulated")
    return {"receipts": ingested, "planted": planted}


def _month_bounds(site: Site, month: str | None) -> tuple[str, datetime, datetime]:
    tz = site_tz(site)
    if month is None:
        today = datetime.now(tz).date()
        year, mon = today.year, today.month
    else:
        try:
            year, mon = int(month[:4]), int(month[5:7])
            if month[4] != "-" or not 1 <= mon <= 12 or len(month) != 7:
                raise ValueError
        except (ValueError, IndexError):
            raise ValidationError("month must look like 2026-08.") from None
    start = datetime(year, mon, 1, tzinfo=tz)
    if mon == 12:
        end = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end = datetime(year, mon + 1, 1, tzinfo=tz)
    return f"{year:04d}-{mon:02d}", start, end


def get_savings(db: Session, site: Site, month: str | None = None) -> SavingsOut:
    tz = site_tz(site)
    month_str, ts_from, ts_to = _month_bounds(site, month)
    zones = list_zones(db, site)

    breaches = _queue_breaches(db, site, zones, ts_from, ts_to, tz)
    queue_count = sum(len(z["breaches"]) for z in breaches)

    after_hours = db.execute(
        text(
            """
            SELECT count(*)
            FROM event e JOIN zone z ON e.zone_id = z.id
            WHERE z.site_id = :site_id
              AND e.type = 'entry'
              AND e.ts_start >= :ts_from AND e.ts_start < :ts_to
              AND (CAST(e.ts_start AT TIME ZONE :tz AS time) >= :closing
                   OR CAST(e.ts_start AT TIME ZONE :tz AS time) < :morning)
            """
        ),
        {
            "site_id": str(site.id),
            "ts_from": ts_from,
            "ts_to": ts_to,
            "tz": site.timezone,
            "closing": site.closing_time,
            "morning": time(6, 0),
        },
    ).scalar()

    unmatched_packages = db.scalar(
        select(func.coalesce(func.sum(Event.attributes["unmatched"].astext.cast(Integer)), 0))
        .join(Camera, Event.camera_id == Camera.id)
        .where(
            Camera.site_id == site.id,
            Event.type == "delivery_trip",
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
    )

    pos_days = [
        row[0]
        for row in db.execute(
            text(
                """
                SELECT DISTINCT (ts AT TIME ZONE :tz)::date
                FROM pos_receipt
                WHERE site_id = :site_id AND ts >= :ts_from AND ts < :ts_to
                """
            ),
            {"tz": site.timezone, "site_id": str(site.id), "ts_from": ts_from, "ts_to": ts_to},
        ).all()
    ]
    pos_count = 0
    pos_amount = 0
    for d in sorted(pos_days):
        for f in _reconcile(db, site, d).flags:
            if f.status == "cleared":
                continue
            pos_count += 1
            if f.receipt is not None and f.receipt.total > 0:
                pos_amount += f.receipt.total
            else:
                pos_amount += settings.savings_value_per_flagged_txn

    lines = [
        SavingsLine(
            key="queues",
            count=queue_count,
            unit_value=settings.savings_avg_check,
            amount=queue_count * settings.savings_avg_check,
        ),
        SavingsLine(
            key="after_hours",
            count=after_hours or 0,
            unit_value=settings.savings_after_hours_value,
            amount=(after_hours or 0) * settings.savings_after_hours_value,
        ),
        SavingsLine(
            key="deliveries",
            count=unmatched_packages or 0,
            unit_value=settings.savings_package_value,
            amount=(unmatched_packages or 0) * settings.savings_package_value,
        ),
        SavingsLine(
            key="pos",
            count=pos_count,
            unit_value=settings.savings_value_per_flagged_txn,
            amount=pos_amount,
        ),
    ]
    total = sum(line.amount for line in lines)
    return SavingsOut(
        month=month_str,
        lines=lines,
        total=total,
        subscription=settings.subscription_price_month,
        net=total - settings.subscription_price_month,
        constants={
            "avg_check": settings.savings_avg_check,
            "value_per_flagged_txn": settings.savings_value_per_flagged_txn,
            "after_hours_value": settings.savings_after_hours_value,
            "package_value": settings.savings_package_value,
            "subscription_price_month": settings.subscription_price_month,
        },
    )
