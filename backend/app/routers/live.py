import re
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.deps import DbDep, LocaleDep, PrincipalDep, SiteDep, require_admin
from app.errors import NotFoundError, ValidationError
from app.models import Camera
from app.schemas import (
    LiveCommentaryOut,
    LiveEventOut,
    LiveEventsOut,
    WorkerStatusOut,
)
from app.scoping import site_camera
from app.services import analytics, replay, worker_status
from app.services.ai import commentary

router = APIRouter(tags=["live"])

_STREAM_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ReplayIn(BaseModel):
    camera_id: uuid.UUID | None = None


def stream_name(rtsp_url: str) -> str:
    name = rtsp_url.rstrip("/").rsplit("/", 1)[-1]
    if not _STREAM_RE.match(name):
        raise ValidationError(f"Cannot derive a relay stream name from {rtsp_url!r}.")
    return name


@router.post("/live/replay", dependencies=[Depends(require_admin)])
def live_replay(db: DbDep, site: SiteDep, body: ReplayIn | None = None):
    if settings.environment != "dev":
        raise NotFoundError("Replay is only available in development.")
    stream = "cam1"
    if body is not None and body.camera_id is not None:
        camera = site_camera(db, site, body.camera_id)
        if camera.role != "cctv":
            raise ValidationError("Replay targets live (cctv) cameras only.")
        stream = stream_name(camera.rtsp_url)
    return replay.start_replay(stream)


@router.get("/live/replay")
def live_replay_status(principal: PrincipalDep):
    return replay.replay_status()


@router.get("/live/events", response_model=LiveEventsOut)
def live_events(db: DbDep, site: SiteDep, after_id: int | None = None, limit: int = 50):
    limit = max(1, min(limit, 100))
    events = analytics.find_recent_events(db, site, after_id, limit)
    zone_names = {z.id: z.name for z in analytics.list_zones(db, site)}
    return LiveEventsOut(
        events=[
            LiveEventOut(
                id=e.id,
                type=e.type,
                zone_name=zone_names.get(e.zone_id),
                ts=e.ts_start,
                attributes=e.attributes or {},
            )
            for e in events
        ],
        latest_id=events[-1].id if events else after_id,
    )


@router.get("/live/workers", response_model=WorkerStatusOut)
def live_workers(db: DbDep, site: SiteDep):
    status = worker_status.read()
    mine = {str(cid) for cid in db.scalars(select(Camera.id).where(Camera.site_id == site.id))}
    return WorkerStatusOut(
        running=status["running"],
        updated_at=status["updated_at"],
        cameras=[c for c in status["cameras"] if c["camera_id"] in mine],
    )


@router.get("/live/commentary", response_model=LiveCommentaryOut)
def live_commentary(db: DbDep, site: SiteDep, locale: LocaleDep, after_id: int | None = None):
    events = analytics.find_recent_events(db, site, after_id, limit=100)
    result = commentary.generate(db, site, events, locale)
    if result.get("skipped") and result.get("reason") == "debounce":
        return LiveCommentaryOut(**result, latest_id=after_id)
    latest = events[-1].id if events else after_id
    return LiveCommentaryOut(**result, latest_id=latest)
