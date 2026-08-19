from datetime import date

from fastapi import APIRouter

from app.deps import DbDep, SiteDep
from app.schemas import EntryFrameOut, LiveMetrics, Summary, TrafficBucket
from app.services import analytics

router = APIRouter(tags=["metrics"])


@router.get("/metrics/live", response_model=LiveMetrics)
def live(db: DbDep, site: SiteDep):
    return analytics.get_live_metrics(db, site)


@router.get("/metrics/traffic", response_model=list[TrafficBucket])
def traffic(db: DbDep, site: SiteDep, day: date | None = None):
    return analytics.get_traffic(db, site, day)


@router.get("/metrics/summary", response_model=Summary)
def summary(db: DbDep, site: SiteDep, day: date | None = None):
    return analytics.get_summary(db, site, day)


@router.get("/metrics/entries", response_model=list[EntryFrameOut])
def entries(db: DbDep, site: SiteDep, day: date | None = None):
    return analytics.get_entry_frames(db, site, day)
