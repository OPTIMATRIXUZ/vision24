from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Float, Integer, func, select, text
from sqlalchemy.orm import Session

from app import storage
from app.models import Alert, AlertRule, Camera, Clip, Event, ProductType, Site, Zone
from app.schemas import (
    DeliveryItem,
    DeliverySummaryOut,
    DeliveryTotal,
    DeliveryTripOut,
    DwellSummary,
    LiveMetrics,
    PeakOccupancy,
    QueueStatus,
    Summary,
    TrafficBucket,
    ZoneOccupancy,
)

MAX_EVENTS = 200
LIVE_SMOOTH_S = 3


def site_tz(site: Site) -> ZoneInfo:
    return ZoneInfo(site.timezone)


def site_day_bounds(site: Site, day: date | None) -> tuple[datetime, datetime]:
    tz = site_tz(site)
    if day is None:
        day = datetime.now(tz).date()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def list_zones(db: Session, site: Site) -> list[Zone]:
    return db.scalars(select(Zone).where(Zone.site_id == site.id)).all()


def resolve_zone(zones: list[Zone], name: str | None) -> Zone | None:
    if not name:
        return None
    return next((z for z in zones if z.name.lower() == name.lower()), None)


def _presign_snapshot(snaps: dict[int, str], event_id: int | None) -> str | None:
    if event_id is None or event_id not in snaps:
        return None
    return storage.presign_get(snaps[event_id])


def get_live_metrics(db: Session, site: Site) -> LiveMetrics:
    camera_ids = db.scalars(select(Camera.id).where(Camera.site_id == site.id)).all()

    total_rows = db.execute(
        select(Event.id, Event.camera_id, Event.attributes, Event.ts_start)
        .where(
            Event.camera_id.in_(camera_ids),
            Event.type == "occupancy",
            Event.zone_id.is_(None),
        )
        .distinct(Event.camera_id)
        .order_by(Event.camera_id, Event.ts_start.desc())
    ).all()
    win_start = datetime.now(ZoneInfo("UTC")) - timedelta(seconds=LIVE_SMOOTH_S)
    win_max = dict(
        db.execute(
            select(
                Event.camera_id,
                func.max(Event.attributes["count"].astext.cast(Integer)),
            )
            .where(
                Event.camera_id.in_(camera_ids),
                Event.type == "occupancy",
                Event.zone_id.is_(None),
                Event.ts_start >= win_start,
            )
            .group_by(Event.camera_id)
        ).all()
    )
    total_occupancy = sum(
        win_max[r.camera_id] if r.camera_id in win_max else r.attributes.get("count", 0)
        for r in total_rows
    )
    total_ts = max((r.ts_start for r in total_rows), default=None)
    total_frame_row = max(total_rows, key=lambda r: r.attributes.get("count", 0), default=None)

    zones = list_zones(db, site)
    rules = {
        r.zone_id: r
        for r in db.scalars(
            select(AlertRule).where(
                AlertRule.zone_id.in_([z.id for z in zones]), AlertRule.is_active
            )
        )
    }

    zone_rows: dict = {}
    queue_rows: dict = {}
    frame_event: dict = {}
    if total_frame_row is not None:
        frame_event["total"] = total_frame_row.id
    for zone in zones:
        for event_type, target in (("occupancy", zone_rows), ("queue_len", queue_rows)):
            if event_type == "queue_len" and zone.kind != "checkout_area":
                continue
            row = db.execute(
                select(Event.attributes, Event.ts_start)
                .where(Event.zone_id == zone.id, Event.type == event_type)
                .order_by(Event.ts_start.desc())
                .limit(1)
            ).first()
            if row is None:
                continue
            target[zone.id] = row
            value_key = "count" if event_type == "occupancy" else "queue_len"
            peak_id = db.scalar(
                select(Event.id)
                .where(Event.zone_id == zone.id, Event.type == event_type)
                .order_by(text(f"(attributes->>'{value_key}')::int DESC"))
                .limit(1)
            )
            frame_event[(zone.id, event_type)] = peak_id

    snaps = snapshot_keys_for_events(db, site, [i for i in frame_event.values() if i is not None])

    per_zone = [
        ZoneOccupancy(
            zone_id=zone.id,
            name=zone.name,
            count=zone_rows[zone.id].attributes.get("count", 0),
            ts=zone_rows[zone.id].ts_start,
            snapshot_url=_presign_snapshot(snaps, frame_event.get((zone.id, "occupancy"))),
        )
        for zone in zones
        if zone.id in zone_rows
    ]
    queues = [
        QueueStatus(
            zone_id=zone.id,
            name=zone.name,
            queue_len=queue_rows[zone.id].attributes.get("queue_len", 0),
            ts=queue_rows[zone.id].ts_start,
            threshold=rules[zone.id].threshold if zone.id in rules else None,
            snapshot_url=_presign_snapshot(snaps, frame_event.get((zone.id, "queue_len"))),
        )
        for zone in zones
        if zone.id in queue_rows
    ]

    return LiveMetrics(
        total_occupancy=total_occupancy,
        ts=total_ts,
        snapshot_url=_presign_snapshot(snaps, frame_event.get("total")),
        per_zone=per_zone,
        queues=queues,
    )


def get_traffic(db: Session, site: Site, day: date | None = None) -> list[TrafficBucket]:
    ts_from, ts_to = site_day_bounds(site, day)
    rows = db.execute(
        text(
            """
            SELECT date_trunc('hour', e.ts_start AT TIME ZONE :tz) AS bucket_start,
                   count(*) AS entries
            FROM event e
            JOIN zone z ON e.zone_id = z.id
            WHERE e.type = 'entry'
              AND z.kind = 'entrance'
              AND z.site_id = :site_id
              AND e.ts_start >= :ts_from AND e.ts_start < :ts_to
            GROUP BY 1 ORDER BY 1
            """
        ),
        {"tz": site.timezone, "site_id": str(site.id), "ts_from": ts_from, "ts_to": ts_to},
    ).all()
    return [TrafficBucket(bucket_start=r.bucket_start, entries=r.entries) for r in rows]


def get_summary(db: Session, site: Site, day: date | None = None) -> Summary:
    ts_from, ts_to = site_day_bounds(site, day)
    camera_ids = db.scalars(select(Camera.id).where(Camera.site_id == site.id)).all()

    entries_total = db.scalar(
        select(func.count(Event.id))
        .join(Zone, Event.zone_id == Zone.id)
        .where(
            Event.type == "entry",
            Zone.kind == "entrance",
            Zone.site_id == site.id,
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
    )
    unique_visitors = db.scalar(
        select(func.count(func.distinct(Event.track_id)))
        .join(Zone, Event.zone_id == Zone.id)
        .where(
            Event.type == "entry",
            Zone.kind == "entrance",
            Zone.site_id == site.id,
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
    )

    peak_row = db.execute(
        select(Event.id, Event.attributes, Event.ts_start, Camera.name.label("camera_name"))
        .join(Camera, Event.camera_id == Camera.id)
        .where(
            Event.camera_id.in_(camera_ids),
            Event.type == "occupancy",
            Event.zone_id.is_(None),
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
        .order_by(text("(attributes->>'count')::int DESC"))
        .limit(1)
    ).first()

    dwell_rows = db.execute(
        select(
            Zone.name,
            func.avg(Event.attributes["dwell_s"].astext.cast(Float)).label("avg_s"),
        )
        .join(Zone, Event.zone_id == Zone.id)
        .where(
            Event.type == "dwell",
            Zone.site_id == site.id,
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
        .group_by(Zone.name)
    ).all()

    snaps = snapshot_keys_for_events(db, site, [peak_row.id] if peak_row else [])
    last_entry_key = db.scalar(
        select(Clip.snapshot_key)
        .join(Event, Clip.event_id == Event.id)
        .join(Zone, Event.zone_id == Zone.id)
        .where(
            Event.type == "entry",
            Zone.kind == "entrance",
            Zone.site_id == site.id,
            Clip.snapshot_key.is_not(None),
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
        .order_by(Event.ts_start.desc())
        .limit(1)
    )
    return Summary(
        entries_total=entries_total or 0,
        unique_visitors=unique_visitors or 0,
        peak_occupancy=PeakOccupancy(
            value=peak_row.attributes.get("count", 0) if peak_row else 0,
            ts=peak_row.ts_start if peak_row else None,
            camera_name=peak_row.camera_name if peak_row else None,
            snapshot_url=_presign_snapshot(snaps, peak_row.id if peak_row else None),
        ),
        avg_dwell=[
            DwellSummary(zone_name=r.name, avg_dwell_s=float(r.avg_s or 0)) for r in dwell_rows
        ],
        last_entry_snapshot_url=storage.presign_get(last_entry_key) if last_entry_key else None,
    )


def find_events(
    db: Session,
    site: Site,
    event_type: str,
    zone: Zone | None,
    ts_from: datetime,
    ts_to: datetime,
) -> list[Event]:
    stmt = (
        select(Event)
        .join(Camera, Event.camera_id == Camera.id)
        .where(
            Camera.site_id == site.id,
            Event.type == event_type,
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
        .order_by(Event.ts_start)
        .limit(MAX_EVENTS)
    )
    if zone is not None:
        stmt = stmt.where(Event.zone_id == zone.id)
    elif event_type == "occupancy":
        stmt = stmt.where(Event.zone_id.is_(None))
    return db.scalars(stmt).all()


def find_recent_events(
    db: Session,
    site: Site,
    after_id: int | None,
    limit: int = 50,
    window_s: int = 300,
) -> list[Event]:
    cutoff = datetime.now(ZoneInfo("UTC")) - timedelta(seconds=window_s)
    stmt = (
        select(Event)
        .join(Camera, Event.camera_id == Camera.id)
        .where(Camera.site_id == site.id, Event.ts_start >= cutoff)
    )
    if after_id is not None:
        stmt = stmt.where(Event.id > after_id)
        return db.scalars(stmt.order_by(Event.id).limit(limit)).all()
    rows = db.scalars(stmt.order_by(Event.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def summarize_events(event_type: str, events: list[Event], zone_names: dict, tz: ZoneInfo) -> str:
    if not events:
        return f"- Matched events of type '{event_type}': 0"
    lines = [f"- Matched events of type '{event_type}': {len(events)}"]

    if event_type in ("occupancy", "queue_len"):
        key = "count" if event_type == "occupancy" else "queue_len"
        values = [(e.attributes.get(key, 0), e.ts_start) for e in events]
        peak_value, peak_ts = max(values, key=lambda v: v[0])
        lines.append(f"- Peak value: {peak_value} at {peak_ts.astimezone(tz):%H:%M %d.%m.%Y}")
    elif event_type == "dwell":
        avg = sum(e.attributes.get("dwell_s", 0) for e in events) / len(events)
        lines.append(f"- Average dwell: {avg:.0f} seconds")
    else:
        unique = len({e.track_id for e in events if e.track_id is not None})
        if unique:
            lines.append(f"- Unique people (distinct tracks): {unique} — visits >= people")
        first, last = events[0], events[-1]
        lines.append(f"- First: {first.ts_start.astimezone(tz):%H:%M:%S %d.%m.%Y}")
        lines.append(f"- Last: {last.ts_start.astimezone(tz):%H:%M:%S %d.%m.%Y}")
        sample = ", ".join(
            f"{e.ts_start.astimezone(tz):%H:%M:%S} ({zone_names.get(e.zone_id, 'store')})"
            for e in events[:10]
        )
        lines.append(f"- Timestamps: {sample}")

    return "\n".join(lines)


def get_entry_frames(
    db: Session, site: Site, day: date | None = None, limit: int = 100
) -> list[dict]:
    ts_from, ts_to = site_day_bounds(site, day)
    rows = db.execute(
        select(Event.id, Event.ts_start, Zone.name, Clip.snapshot_key)
        .join(Zone, Event.zone_id == Zone.id)
        .outerjoin(Clip, Clip.event_id == Event.id)
        .where(
            Event.type == "entry",
            Zone.site_id == site.id,
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
        .order_by(Event.ts_start)
        .limit(limit)
    ).all()
    return [
        {
            "event_id": r.id,
            "ts": r.ts_start,
            "zone_name": r.name,
            "snapshot_url": storage.presign_get(r.snapshot_key) if r.snapshot_key else None,
        }
        for r in rows
    ]


def get_delivery_trips(
    db: Session, site: Site, day: date | None = None, camera_id=None
) -> list[DeliveryTripOut]:
    ts_from, ts_to = site_day_bounds(site, day)
    stmt = (
        select(Event, Camera.name, Zone.name.label("zone_name"))
        .join(Camera, Event.camera_id == Camera.id)
        .outerjoin(Zone, Event.zone_id == Zone.id)
        .where(
            Camera.site_id == site.id,
            Event.type == "delivery_trip",
            Event.ts_start >= ts_from,
            Event.ts_start < ts_to,
        )
        .order_by(Event.ts_start)
        .limit(MAX_EVENTS)
    )
    if camera_id is not None:
        stmt = stmt.where(Event.camera_id == camera_id)
    rows = db.execute(stmt).all()
    snaps = snapshot_keys_for_events(db, site, [r.Event.id for r in rows])
    return [
        DeliveryTripOut(
            event_id=r.Event.id,
            camera_name=r.name,
            zone_name=r.zone_name,
            ts_start=r.Event.ts_start,
            ts_end=r.Event.ts_end,
            items=[DeliveryItem(**item) for item in r.Event.attributes.get("items", [])],
            unmatched=r.Event.attributes.get("unmatched", 0),
            snapshot_url=_presign_snapshot(snaps, r.Event.id),
            crop_url=(
                storage.presign_get(r.Event.attributes["crop_key"])
                if r.Event.attributes.get("crop_key")
                else None
            ),
        )
        for r in rows
    ]


def get_delivery_summary(
    db: Session, site: Site, day: date | None = None, camera_id=None
) -> DeliverySummaryOut:
    trips = get_delivery_trips(db, site, day, camera_id)
    if day is None:
        day = datetime.now(site_tz(site)).date()

    products = {
        str(p.id): p for p in db.scalars(select(ProductType).where(ProductType.site_id == site.id))
    }
    packages: dict[tuple[str | None, str], int] = {}
    unmatched = 0
    for trip in trips:
        unmatched += trip.unmatched
        for item in trip.items:
            key = (item.product_type_id, item.product_name)
            packages[key] = packages.get(key, 0) + item.count

    totals = []
    for (product_id, name), count in sorted(packages.items(), key=lambda kv: -kv[1]):
        product = products.get(product_id) if product_id else None
        units = None
        if product is not None and product.units_per_package:
            units = count * product.units_per_package
        totals.append(
            DeliveryTotal(
                product_type_id=product_id,
                product_name=name,
                packages=count,
                units=units,
                unit_label=product.unit_label if product is not None else None,
            )
        )
    return DeliverySummaryOut(
        day=day.isoformat(), trips=trips, totals=totals, unmatched_packages=unmatched
    )


def snapshot_keys_for_events(db: Session, site: Site, event_ids: list[int]) -> dict[int, str]:
    if not event_ids:
        return {}
    rows = db.execute(
        select(Clip.event_id, Clip.snapshot_key)
        .join(Event, Clip.event_id == Event.id)
        .join(Camera, Event.camera_id == Camera.id)
        .where(
            Clip.event_id.in_(event_ids),
            Clip.snapshot_key.is_not(None),
            Camera.site_id == site.id,
        )
    ).all()
    return {r.event_id: r.snapshot_key for r in rows}


def get_clips_for_events(db: Session, site: Site, event_ids: list[int], limit: int) -> list[Clip]:
    if not event_ids:
        return []
    rows = db.scalars(
        select(Clip)
        .join(Event, Clip.event_id == Event.id)
        .join(Camera, Event.camera_id == Camera.id)
        .where(
            Clip.event_id.in_(event_ids),
            Clip.storage_key.is_not(None),
            Camera.site_id == site.id,
        )
        .order_by(Clip.ts_start)
    ).all()
    return rows[:limit]


def get_alerts(db: Session, site: Site, limit: int = 10) -> list[Alert]:
    zone_ids = select(Zone.id).where(Zone.site_id == site.id)
    rule_ids = select(AlertRule.id).where(AlertRule.zone_id.in_(zone_ids))
    return db.scalars(
        select(Alert)
        .where(Alert.rule_id.in_(rule_ids))
        .order_by(Alert.triggered_at.desc())
        .limit(limit)
    ).all()


def build_report_context(db: Session, site: Site, day: date | None = None) -> dict:
    tz = site_tz(site)
    if day is None:
        day = datetime.now(tz).date()
    ts_from, ts_to = site_day_bounds(site, day)
    zones = list_zones(db, site)
    zone_ids = [z.id for z in zones]

    summary = get_summary(db, site, day)
    traffic = get_traffic(db, site, day)

    entries_by_zone = dict(
        db.execute(
            select(Event.zone_id, func.count(Event.id))
            .where(
                Event.zone_id.in_(zone_ids),
                Event.type == "entry",
                Event.ts_start >= ts_from,
                Event.ts_start < ts_to,
            )
            .group_by(Event.zone_id)
        ).all()
    )
    dwell_by_zone = {
        r.zone_id: (float(r.avg_s or 0), float(r.max_s or 0))
        for r in db.execute(
            select(
                Event.zone_id,
                func.avg(Event.attributes["dwell_s"].astext.cast(Float)).label("avg_s"),
                func.max(Event.attributes["dwell_s"].astext.cast(Float)).label("max_s"),
            )
            .where(
                Event.zone_id.in_(zone_ids),
                Event.type == "dwell",
                Event.ts_start >= ts_from,
                Event.ts_start < ts_to,
            )
            .group_by(Event.zone_id)
        ).all()
    }
    peak_occ_by_zone = dict(
        db.execute(
            select(
                Event.zone_id,
                func.max(Event.attributes["count"].astext.cast(Integer)),
            )
            .where(
                Event.zone_id.in_(zone_ids),
                Event.type == "occupancy",
                Event.ts_start >= ts_from,
                Event.ts_start < ts_to,
            )
            .group_by(Event.zone_id)
        ).all()
    )

    zones_ctx = []
    for z in zones:
        avg_dwell, max_dwell = dwell_by_zone.get(z.id, (0.0, 0.0))
        zones_ctx.append(
            {
                "name": z.name,
                "kind": z.kind,
                "entries": entries_by_zone.get(z.id, 0),
                "peak_occupancy": peak_occ_by_zone.get(z.id, 0),
                "avg_dwell_s": round(avg_dwell),
                "max_dwell_s": round(max_dwell),
            }
        )

    queues_ctx = _queue_breaches(db, site, zones, ts_from, ts_to, tz)
    alerts_ctx = [
        {
            "time": a.triggered_at.astimezone(tz).strftime("%H:%M"),
            "message": a.message,
            "value": a.value,
        }
        for a in db.scalars(
            select(Alert)
            .where(
                Alert.rule_id.in_(select(AlertRule.id).where(AlertRule.zone_id.in_(zone_ids))),
                Alert.triggered_at >= ts_from,
                Alert.triggered_at < ts_to,
            )
            .order_by(Alert.triggered_at)
        ).all()
    ]

    ah_from = datetime.combine(day, site.closing_time, tzinfo=tz)
    ah_to = datetime.combine(day + timedelta(days=1), time(6, 0), tzinfo=tz)
    zone_names = {z.id: z.name for z in zones}
    after_hours = [
        {
            "time": e.ts_start.astimezone(tz).strftime("%H:%M:%S"),
            "zone": zone_names.get(e.zone_id, "store"),
        }
        for e in db.scalars(
            select(Event)
            .where(
                Event.zone_id.in_(zone_ids),
                Event.type == "entry",
                Event.ts_start >= ah_from,
                Event.ts_start < ah_to,
            )
            .order_by(Event.ts_start)
            .limit(50)
        ).all()
    ]

    delivery = get_delivery_summary(db, site, day)
    deliveries_ctx = {
        "trips": len(delivery.trips),
        "unmatched_packages": delivery.unmatched_packages,
        "products": [
            {
                "name": t.product_name,
                "packages": t.packages,
                "units": t.units,
                "unit_label": t.unit_label,
            }
            for t in delivery.totals
        ],
    }

    from app.services import pos as pos_service

    pos_out = pos_service.get_discrepancies(db, site, day)
    pos_ctx = {
        "receipts": pos_out.receipts_total,
        "feed_note": "simulated Flowpos feed (real API format)",
        "discrepancies": [
            {
                "flag": d.flag,
                "time": d.ts.astimezone(tz).strftime("%H:%M:%S"),
                "zone": d.zone_name,
                "total_uzs": d.receipt.total if d.receipt else None,
                "explanation": d.explanation,
            }
            for d in pos_out.discrepancies
        ],
    }

    savings = pos_service.get_savings(db, site, day.strftime("%Y-%m"))
    savings_ctx = {
        "month": savings.month,
        "currency": "UZS",
        "lines": [
            {"key": line.key, "count": line.count, "amount": line.amount}
            for line in savings.lines
            if line.count
        ],
        "total": savings.total,
        "subscription": savings.subscription,
        "net": savings.net,
    }

    return {
        "date": day.isoformat(),
        "site": site.name,
        "timezone": site.timezone,
        "closing_time": site.closing_time.strftime("%H:%M"),
        "entries_total": summary.entries_total,
        "peak_occupancy": {
            "value": summary.peak_occupancy.value,
            "time": summary.peak_occupancy.ts.astimezone(tz).strftime("%H:%M")
            if summary.peak_occupancy.ts
            else None,
        },
        "hourly_traffic": [
            {"hour": b.bucket_start.strftime("%H:%M"), "entries": b.entries} for b in traffic
        ],
        "zones": zones_ctx,
        "queues": queues_ctx,
        "alerts": alerts_ctx,
        "after_hours_entries": after_hours,
        "deliveries": deliveries_ctx,
        "pos": pos_ctx,
        "savings": savings_ctx,
    }


def _queue_breaches(
    db: Session, site: Site, zones: list[Zone], ts_from: datetime, ts_to: datetime, tz: ZoneInfo
) -> list[dict]:
    out = []
    for zone in zones:
        if zone.kind != "checkout_area":
            continue
        rule = db.scalars(
            select(AlertRule).where(
                AlertRule.zone_id == zone.id,
                AlertRule.metric == "queue_len",
                AlertRule.is_active,
            )
        ).first()
        samples = db.execute(
            select(Event.ts_start, Event.attributes)
            .where(
                Event.zone_id == zone.id,
                Event.type == "queue_len",
                Event.ts_start >= ts_from,
                Event.ts_start < ts_to,
            )
            .order_by(Event.ts_start)
        ).all()
        if not samples:
            continue
        values = [(r.ts_start, r.attributes.get("queue_len", 0)) for r in samples]
        max_len = max(v for _, v in values)
        breaches = []
        if rule is not None:
            window = None
            for ts, v in values:
                if v >= rule.threshold:
                    if window is None:
                        window = [ts, ts, v]
                    else:
                        window[1], window[2] = ts, max(window[2], v)
                elif window is not None:
                    breaches.append(window)
                    window = None
            if window is not None:
                breaches.append(window)
        out.append(
            {
                "zone": zone.name,
                "threshold": rule.threshold if rule else None,
                "max_queue_len": max_len,
                "breaches": [
                    {
                        "from": w[0].astimezone(tz).strftime("%H:%M"),
                        "to": w[1].astimezone(tz).strftime("%H:%M"),
                        "peak": w[2],
                    }
                    for w in breaches
                ],
            }
        )
    return out
