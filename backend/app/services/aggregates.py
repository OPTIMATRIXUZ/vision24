import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Site, SiteDailyStats
from app.services import analytics

log = logging.getLogger(__name__)


def compute_daily_stats(db: Session, site: Site, day: date | None = None) -> SiteDailyStats:
    tz = analytics.site_tz(site)
    if day is None:
        day = datetime.now(tz).date()

    summary = analytics.get_summary(db, site, day)
    traffic = analytics.get_traffic(db, site, day)
    peak_hour = max(traffic, key=lambda b: b.entries).bucket_start.hour if traffic else None
    dwell = summary.avg_dwell
    avg_dwell_s = sum(d.avg_dwell_s for d in dwell) / len(dwell) if dwell else None

    ts_from, ts_to = analytics.site_day_bounds(site, day)
    zones = analytics.list_zones(db, site)
    breaches = analytics._queue_breaches(db, site, zones, ts_from, ts_to, tz)
    queue_breaches = sum(len(z["breaches"]) for z in breaches)
    alerts_count = len(
        [a for a in analytics.get_alerts(db, site, limit=1000) if ts_from <= a.triggered_at < ts_to]
    )

    row = db.scalars(
        select(SiteDailyStats).where(SiteDailyStats.site_id == site.id, SiteDailyStats.day == day)
    ).first()
    if row is None:
        row = SiteDailyStats(site_id=site.id, day=day, visitors=0, peak_occupancy=0)
        db.add(row)
    row.visitors = summary.entries_total
    row.peak_occupancy = summary.peak_occupancy.value
    row.peak_hour = peak_hour
    row.avg_dwell_s = avg_dwell_s
    row.queue_breaches = queue_breaches
    row.alerts_count = alerts_count
    row.computed_at = datetime.now(tz)
    db.commit()
    return row


def compute_daily_stats_safe(db: Session, site: Site, day: date | None = None) -> None:
    try:
        compute_daily_stats(db, site, day)
    except Exception:
        log.exception("Daily stats aggregation failed for site %s", site.id)
