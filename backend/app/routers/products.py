import logging
import uuid

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import delete, func, select

from app import storage
from app.deps import DbDep, SiteDep, require_admin
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import ProductSample, ProductType
from app.schemas import ProductSampleOut, ProductTypeIn, ProductTypeOut
from app.scoping import site_product

log = logging.getLogger(__name__)

router = APIRouter(tags=["products"])

MAX_SAMPLES = 5
SAMPLE_MAX_SIDE = 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _product_out(product: ProductType, samples: list[ProductSample]) -> ProductTypeOut:
    return ProductTypeOut(
        id=product.id,
        name=product.name,
        units_per_package=product.units_per_package,
        unit_label=product.unit_label,
        samples=[
            ProductSampleOut(id=s.id, url=storage.presign_get(s.storage_key)) for s in samples
        ],
    )


def ensure_sample_capacity(db, product: ProductType) -> None:
    count = db.scalar(
        select(func.count())
        .select_from(ProductSample)
        .where(ProductSample.product_type_id == product.id)
    )
    if count >= MAX_SAMPLES:
        raise ValidationError(f"A product can have at most {MAX_SAMPLES} sample photos.")


def _name_taken(db, site_id, name: str, exclude_id=None) -> bool:
    stmt = select(ProductType.id).where(
        ProductType.site_id == site_id, func.lower(ProductType.name) == name.lower()
    )
    if exclude_id is not None:
        stmt = stmt.where(ProductType.id != exclude_id)
    return db.scalars(stmt).first() is not None


@router.get("/products", response_model=list[ProductTypeOut])
def list_products(db: DbDep, site: SiteDep):
    products = db.scalars(
        select(ProductType).where(ProductType.site_id == site.id).order_by(ProductType.created_at)
    ).all()
    samples_by_product: dict[uuid.UUID, list[ProductSample]] = {}
    if products:
        rows = db.scalars(
            select(ProductSample)
            .where(ProductSample.product_type_id.in_([p.id for p in products]))
            .order_by(ProductSample.created_at)
        ).all()
        for sample in rows:
            samples_by_product.setdefault(sample.product_type_id, []).append(sample)
    return [_product_out(p, samples_by_product.get(p.id, [])) for p in products]


@router.post("/products", response_model=ProductTypeOut, dependencies=[Depends(require_admin)])
def create_product(body: ProductTypeIn, db: DbDep, site: SiteDep):
    if _name_taken(db, site.id, body.name):
        raise ConflictError(f"A product named {body.name!r} already exists.")
    product = ProductType(
        site_id=site.id,
        name=body.name.strip(),
        units_per_package=body.units_per_package,
        unit_label=body.unit_label,
    )
    db.add(product)
    db.commit()
    return _product_out(product, [])


@router.put(
    "/products/{product_id}", response_model=ProductTypeOut, dependencies=[Depends(require_admin)]
)
def update_product(product_id: uuid.UUID, body: ProductTypeIn, db: DbDep, site: SiteDep):
    product = site_product(db, site, product_id)
    if _name_taken(db, site.id, body.name, exclude_id=product.id):
        raise ConflictError(f"A product named {body.name!r} already exists.")
    product.name = body.name.strip()
    product.units_per_package = body.units_per_package
    product.unit_label = body.unit_label
    db.commit()
    samples = db.scalars(
        select(ProductSample)
        .where(ProductSample.product_type_id == product.id)
        .order_by(ProductSample.created_at)
    ).all()
    return _product_out(product, samples)


@router.delete("/products/{product_id}", dependencies=[Depends(require_admin)])
def delete_product(product_id: uuid.UUID, db: DbDep, site: SiteDep):
    product = site_product(db, site, product_id)
    doomed_keys = list(
        db.scalars(
            select(ProductSample.storage_key).where(ProductSample.product_type_id == product.id)
        )
    )
    db.execute(delete(ProductSample).where(ProductSample.product_type_id == product.id))
    db.delete(product)
    db.commit()
    for key in doomed_keys:
        storage.remove_object(key)
    return {"deleted": str(product_id)}


@router.post(
    "/products/{product_id}/samples",
    response_model=ProductSampleOut,
    dependencies=[Depends(require_admin)],
)
def add_sample(product_id: uuid.UUID, file: UploadFile, db: DbDep, site: SiteDep):
    product = site_product(db, site, product_id)
    ensure_sample_capacity(db, product)
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError("Sample photos must be JPEG, PNG or WebP images.")

    import cv2
    import numpy as np

    data = file.file.read()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValidationError("That file is not a readable image.")
    h, w = img.shape[:2]
    if max(h, w) > SAMPLE_MAX_SIDE:
        scale = SAMPLE_MAX_SIDE / max(h, w)
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
    ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValidationError("Could not process that image.")

    key = f"product-samples/{product.id}/{uuid.uuid4().hex}.jpg"
    storage.upload_bytes(key, jpeg.tobytes(), "image/jpeg")
    sample = ProductSample(product_type_id=product.id, storage_key=key)
    db.add(sample)
    db.commit()
    return ProductSampleOut(id=sample.id, url=storage.presign_get(key))


@router.delete("/products/{product_id}/samples/{sample_id}", dependencies=[Depends(require_admin)])
def delete_sample(product_id: uuid.UUID, sample_id: uuid.UUID, db: DbDep, site: SiteDep):
    product = site_product(db, site, product_id)
    sample = db.scalars(
        select(ProductSample).where(
            ProductSample.id == sample_id, ProductSample.product_type_id == product.id
        )
    ).first()
    if sample is None:
        raise NotFoundError("Sample not found.")
    key = sample.storage_key
    db.delete(sample)
    db.commit()
    storage.remove_object(key)
    return {"deleted": str(sample_id)}
