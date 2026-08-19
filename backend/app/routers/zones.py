import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.deps import DbDep, SiteDep, require_admin
from app.models import AlertRule, Zone
from app.schemas import ZoneIn, ZoneOut
from app.scoping import site_camera, site_zone

DEFAULT_QUEUE_THRESHOLD = 3
DEFAULT_QUEUE_SUSTAIN_S = 15

FULL_FRAME = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

router = APIRouter(tags=["zones"])


def create_zone_with_rule(
    db,
    site_id,
    camera_id,
    name: str,
    kind: str,
    polygon: list,
    record_clips: bool,
    privacy_mask: bool = False,
) -> Zone:
    zone = Zone(
        site_id=site_id,
        camera_id=camera_id,
        name=name,
        kind=kind,
        polygon=polygon,
        record_clips=record_clips,
        privacy_mask=privacy_mask,
    )
    db.add(zone)
    db.flush()
    if zone.kind == "checkout_area":
        db.add(
            AlertRule(
                zone_id=zone.id,
                metric="queue_len",
                threshold=DEFAULT_QUEUE_THRESHOLD,
                sustain_seconds=DEFAULT_QUEUE_SUSTAIN_S,
            )
        )
    db.commit()
    return zone


@router.get("/zones", response_model=list[ZoneOut])
def list_zones(db: DbDep, site: SiteDep):
    return db.scalars(select(Zone).where(Zone.site_id == site.id)).all()


@router.post("/zones", response_model=ZoneOut, dependencies=[Depends(require_admin)])
def create_zone(body: ZoneIn, db: DbDep, site: SiteDep):
    site_camera(db, site, body.camera_id)
    return create_zone_with_rule(
        db,
        site_id=site.id,
        camera_id=body.camera_id,
        name=body.name,
        kind=body.kind,
        polygon=body.polygon,
        record_clips=body.record_clips,
        privacy_mask=body.privacy_mask,
    )


@router.put("/zones/{zone_id}", response_model=ZoneOut, dependencies=[Depends(require_admin)])
def update_zone(zone_id: uuid.UUID, body: ZoneIn, db: DbDep, site: SiteDep):
    zone = site_zone(db, site, zone_id)
    site_camera(db, site, body.camera_id)
    for key, value in body.model_dump().items():
        setattr(zone, key, value)
    db.commit()
    return zone


@router.delete("/zones/{zone_id}", dependencies=[Depends(require_admin)])
def delete_zone(zone_id: uuid.UUID, db: DbDep, site: SiteDep):
    zone = site_zone(db, site, zone_id)
    db.delete(zone)
    db.commit()
    return {"deleted": str(zone_id)}
