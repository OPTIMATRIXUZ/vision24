import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app import storage
from app.deps import DbDep, SiteDep, require_admin
from app.errors import NotFoundError
from app.models import Camera, Event, ProductSample
from app.routers.products import ensure_sample_capacity
from app.schemas import DeliverySummaryOut, ProductSampleOut, SaveTripSampleIn
from app.scoping import site_camera, site_product
from app.services import analytics

router = APIRouter(tags=["deliveries"])


@router.get("/deliveries", response_model=DeliverySummaryOut)
def get_deliveries(
    db: DbDep, site: SiteDep, day: date | None = None, camera_id: uuid.UUID | None = None
):
    if camera_id is not None:
        site_camera(db, site, camera_id)
    return analytics.get_delivery_summary(db, site, day, camera_id)


@router.post(
    "/deliveries/{event_id}/sample",
    response_model=ProductSampleOut,
    dependencies=[Depends(require_admin)],
)
def save_trip_sample(event_id: int, body: SaveTripSampleIn, db: DbDep, site: SiteDep):
    event = db.scalars(
        select(Event)
        .join(Camera, Event.camera_id == Camera.id)
        .where(Event.id == event_id, Event.type == "delivery_trip", Camera.site_id == site.id)
    ).first()
    if event is None:
        raise NotFoundError("Delivery trip not found.")
    crop_key = (event.attributes or {}).get("crop_key")
    if not crop_key:
        raise NotFoundError("This trip has no stored package crop.")

    product = site_product(db, site, body.product_type_id)
    ensure_sample_capacity(db, product)

    data = storage.download_bytes(crop_key)
    key = f"product-samples/{product.id}/{uuid.uuid4().hex}.jpg"
    storage.upload_bytes(key, data, "image/jpeg")
    sample = ProductSample(product_type_id=product.id, storage_key=key)
    db.add(sample)
    db.commit()
    return ProductSampleOut(id=sample.id, url=storage.presign_get(key))
