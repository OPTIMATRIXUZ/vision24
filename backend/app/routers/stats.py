import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import DbDep, PrincipalDep, SiteDep, require_admin, require_owner
from app.models import SiteDailyStats, Tenant
from app.services import aggregates

router = APIRouter(tags=["stats"])


class DailyStatsOut(BaseModel):
    site_id: uuid.UUID
    day: date
    visitors: int
    peak_occupancy: int
    peak_hour: int | None
    avg_dwell_s: float | None
    queue_breaches: int
    alerts_count: int

    model_config = {"from_attributes": True}


class AggregateIn(BaseModel):
    day: date | None = None


class OptInIn(BaseModel):
    opt_in: bool


@router.post(
    "/stats/aggregate", response_model=DailyStatsOut, dependencies=[Depends(require_admin)]
)
def aggregate(body: AggregateIn, db: DbDep, site: SiteDep):
    return aggregates.compute_daily_stats(db, site, body.day)


@router.get("/stats/daily", response_model=list[DailyStatsOut])
def list_daily(
    db: DbDep,
    site: SiteDep,
    date_from: date | None = None,
    date_to: date | None = None,
):
    stmt = select(SiteDailyStats).where(SiteDailyStats.site_id == site.id)
    if date_from is not None:
        stmt = stmt.where(SiteDailyStats.day >= date_from)
    if date_to is not None:
        stmt = stmt.where(SiteDailyStats.day <= date_to)
    return db.scalars(stmt.order_by(SiteDailyStats.day.desc()).limit(400)).all()


@router.get("/stats/opt-in")
def get_opt_in(db: DbDep, principal: PrincipalDep):
    value = db.scalar(select(Tenant.stats_opt_in).where(Tenant.id == principal.tenant_id))
    return {"opt_in": bool(value)}


@router.put("/stats/opt-in", dependencies=[Depends(require_owner)])
def set_opt_in(body: OptInIn, db: DbDep, principal: PrincipalDep):
    tenant = db.get(Tenant, principal.tenant_id)
    tenant.stats_opt_in = body.opt_in
    db.add(tenant)
    db.commit()
    return {"opt_in": tenant.stats_opt_in}
