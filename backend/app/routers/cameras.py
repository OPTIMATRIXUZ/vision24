import uuid

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.deps import DbDep, SiteDep
from app.models import Camera
from app.schemas import CameraOut
from app.scoping import site_camera
from app.services import capture as capture_service

router = APIRouter(tags=["cameras"])


@router.get("/cameras", response_model=list[CameraOut])
def list_cameras(db: DbDep, site: SiteDep):
    return db.scalars(select(Camera).where(Camera.site_id == site.id)).all()


@router.get("/cameras/{camera_id}/snapshot")
def camera_snapshot(camera_id: uuid.UUID, db: DbDep, site: SiteDep):
    camera = site_camera(db, site, camera_id)
    jpeg = capture_service.snapshot_bytes(camera)
    return Response(content=jpeg, media_type="image/jpeg")
