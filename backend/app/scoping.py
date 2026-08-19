import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models import Camera, ProductType, Site, Zone


def resolve_site(
    db: Session,
    tenant_id: uuid.UUID,
    site_id: uuid.UUID | None = None,
) -> Site:
    stmt = select(Site).where(Site.tenant_id == tenant_id)
    if site_id is not None:
        stmt = stmt.where(Site.id == site_id)
    else:
        stmt = stmt.order_by(Site.created_at, Site.id)

    site = db.scalars(stmt.limit(1)).first()
    if site is None:
        if site_id is not None:
            raise NotFoundError("Site not found.")
        raise NotFoundError("No site configured for this tenant.")
    return site


def site_camera(db: Session, site: Site, camera_id: uuid.UUID) -> Camera:
    camera = db.scalars(
        select(Camera).where(Camera.id == camera_id, Camera.site_id == site.id)
    ).first()
    if camera is None:
        raise NotFoundError("Camera not found.")
    return camera


def site_zone(db: Session, site: Site, zone_id: uuid.UUID) -> Zone:
    zone = db.scalars(select(Zone).where(Zone.id == zone_id, Zone.site_id == site.id)).first()
    if zone is None:
        raise NotFoundError("Zone not found.")
    return zone


def site_product(db: Session, site: Site, product_id: uuid.UUID) -> ProductType:
    product = db.scalars(
        select(ProductType).where(ProductType.id == product_id, ProductType.site_id == site.id)
    ).first()
    if product is None:
        raise NotFoundError("Product not found.")
    return product


def site_zone_ids(db: Session, site: Site) -> list[uuid.UUID]:
    return list(db.scalars(select(Zone.id).where(Zone.site_id == site.id)))
