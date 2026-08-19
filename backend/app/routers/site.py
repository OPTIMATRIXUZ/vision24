from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.deps import DbDep, PrincipalDep, SiteDep, require_admin
from app.errors import ValidationError
from app.models import Site
from app.schemas import SiteOut, SiteUpdateIn

router = APIRouter(tags=["site"])


@router.get("/sites", response_model=list[SiteOut])
def list_sites(db: DbDep, principal: PrincipalDep):
    stmt = select(Site).where(Site.tenant_id == principal.tenant_id)
    if principal.site_id is not None:
        stmt = stmt.where(Site.id == principal.site_id)
    return db.scalars(stmt.order_by(Site.created_at, Site.id)).all()


@router.get("/site", response_model=SiteOut)
def get_site(site: SiteDep):
    return site


@router.put("/site", response_model=SiteOut, dependencies=[Depends(require_admin)])
def update_site(body: SiteUpdateIn, db: DbDep, site: SiteDep):
    try:
        ZoneInfo(body.timezone)
    except Exception as exc:
        raise ValidationError(f"Unknown timezone {body.timezone!r}.") from exc
    site.timezone = body.timezone
    site.closing_time = body.closing_time
    db.commit()
    return site
