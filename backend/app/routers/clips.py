import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app import storage
from app.deps import DbDep, SiteDep
from app.errors import NotFoundError
from app.models import Camera, Clip, Event
from app.schemas import ClipUrl

router = APIRouter(tags=["clips"])


@router.get("/clips/{clip_id}/url", response_model=ClipUrl)
def clip_url(clip_id: uuid.UUID, db: DbDep, site: SiteDep):
    clip = db.scalars(
        select(Clip)
        .join(Event, Clip.event_id == Event.id)
        .join(Camera, Event.camera_id == Camera.id)
        .where(Clip.id == clip_id, Camera.site_id == site.id)
    ).first()
    if clip is None:
        raise NotFoundError("Clip not found.")
    if not clip.storage_key:
        raise NotFoundError("This clip has no stored video.")
    return ClipUrl(
        url=storage.presign_get(clip.storage_key),
        snapshot_url=storage.presign_get(clip.snapshot_key) if clip.snapshot_key else None,
    )
