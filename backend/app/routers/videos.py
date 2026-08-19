import logging
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import delete, select

from app import storage
from app.config import settings
from app.db import SessionLocal
from app.deps import DbDep, PrincipalDep, SiteDep, require_admin, require_owner
from app.errors import JobBusyError, NotFoundError, UnavailableError, ValidationError
from app.models import (
    Alert,
    AlertRule,
    Camera,
    Clip,
    Embedding,
    Event,
    PosReceipt,
    ProductSample,
    ProductType,
    Site,
    SiteDailyStats,
    Tenant,
    Zone,
)
from app.schemas import AnalyzeIn, ResetIn
from app.scoping import site_camera
from app.services import capture as capture_service
from app.services import jobs
from app.services.ai import chat as chat_service
from app.services.ai import report as report_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["videos"])

UPLOAD_DIR = settings.media_path / "uploads"
ALLOWED_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def source_path(camera: Camera) -> Path:
    if camera.role == "cctv":
        return capture_service.capture_path(camera.id)
    return Path(camera.rtsp_url)


def submit_analysis(db, camera: Camera, anchor_end=None):
    path = source_path(camera)
    if not path.exists():
        if camera.role == "cctv":
            raise NotFoundError("No captured segment yet — use Capture & analyze.")
        raise NotFoundError("The source video file is missing.")
    cam_id = camera.id

    def exists_check() -> bool:
        with SessionLocal() as db:
            return db.get(Camera, cam_id) is not None

    def run(handle: jobs.JobHandle) -> None:
        from worker.batch import analyze_video

        handle.set_state("running")
        result = analyze_video(
            cam_id, str(path), progress_cb=handle.set_progress, anchor_end=anchor_end
        )
        log.info("Analysis of %s finished: %s", cam_id, result)
        from app.services import aggregates

        with SessionLocal() as job_db:
            cam = job_db.get(Camera, cam_id)
            if cam is not None:
                site = job_db.get(Site, cam.site_id)
                if site is not None:
                    aggregates.compute_daily_stats_safe(job_db, site)

    return jobs.submit(db, cam_id, "analyze", run, exists_check)


@router.post("/reset", dependencies=[Depends(require_owner)])
def reset_all(body: ResetIn, db: DbDep, principal: PrincipalDep):
    if not settings.allow_reset:
        raise UnavailableError("Resetting is disabled on this deployment.")

    slug = db.scalar(select(Tenant.slug).where(Tenant.id == principal.tenant_id))
    if body.confirm.strip() != slug:
        raise ValidationError(f"To confirm, type the tenant slug {slug!r} in the confirm field.")

    site_id_rows = list(db.scalars(select(Site.id).where(Site.tenant_id == principal.tenant_id)))
    cameras = list(db.scalars(select(Camera).where(Camera.site_id.in_(site_id_rows))))
    camera_ids = [c.id for c in cameras]

    if jobs.any_active(db, camera_ids):
        raise JobBusyError("A job is running — wait for it to finish.")
    jobs.clear(db, camera_ids)

    event_ids = select(Event.id).where(Event.camera_id.in_(camera_ids))
    zone_ids = select(Zone.id).where(Zone.site_id.in_(site_id_rows))

    doomed_keys: list[str] = []
    for clip in db.scalars(select(Clip).where(Clip.event_id.in_(event_ids))):
        doomed_keys.extend(k for k in (clip.storage_key, clip.snapshot_key) if k)
    doomed_keys.extend(
        key
        for key in db.scalars(
            select(Event.attributes["crop_key"].astext).where(
                Event.camera_id.in_(camera_ids), Event.type == "delivery_trip"
            )
        )
        if key
    )
    product_ids = select(ProductType.id).where(ProductType.site_id.in_(site_id_rows))
    doomed_keys.extend(
        db.scalars(
            select(ProductSample.storage_key).where(ProductSample.product_type_id.in_(product_ids))
        )
    )
    for cam in cameras:
        doomed_keys.append(f"processed/{cam.id}.mp4")
        doomed_keys.append(f"heatmaps/{cam.id}.jpg")

    doomed_files = [Path(c.rtsp_url) for c in cameras if c.role == "upload"]
    doomed_files += [capture_service.capture_path(cid) for cid in camera_ids]

    db.execute(delete(Alert).where(Alert.event_id.in_(event_ids)))
    db.execute(
        delete(Alert).where(
            Alert.rule_id.in_(select(AlertRule.id).where(AlertRule.zone_id.in_(zone_ids)))
        )
    )
    db.execute(delete(Embedding).where(Embedding.event_id.in_(event_ids)))
    db.execute(delete(Clip).where(Clip.event_id.in_(event_ids)))
    db.execute(delete(Event).where(Event.camera_id.in_(camera_ids)))
    db.execute(delete(PosReceipt).where(PosReceipt.site_id.in_(site_id_rows)))
    db.execute(delete(SiteDailyStats).where(SiteDailyStats.site_id.in_(site_id_rows)))
    db.execute(delete(AlertRule).where(AlertRule.zone_id.in_(zone_ids)))
    db.execute(delete(Zone).where(Zone.id.in_(zone_ids)))
    db.execute(delete(Camera).where(Camera.id.in_(camera_ids)))
    db.execute(delete(ProductSample).where(ProductSample.product_type_id.in_(product_ids)))
    db.execute(delete(ProductType).where(ProductType.site_id.in_(site_id_rows)))
    db.commit()

    chat_service.clear_sessions(principal.tenant_id)
    for sid in site_id_rows:
        report_service.clear_cache(sid)

    for key in doomed_keys:
        storage.remove_object(key)
    removed_files = 0
    for path in doomed_files:
        if path.is_file():
            path.unlink()
            removed_files += 1

    log.info("Reset: %d files, %d stored objects removed", removed_files, len(doomed_keys))
    return {
        "status": "reset",
        "files_removed": removed_files,
        "objects_removed": len(doomed_keys),
    }


@router.post("/videos", dependencies=[Depends(require_admin)])
def upload_video(file: UploadFile, db: DbDep, site: SiteDep):
    suffix = Path(file.filename or "video.mp4").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValidationError(f"Unsupported file type {suffix!r}.")

    dest, duration_s, fps = save_upload(file)

    camera = Camera(
        site_id=site.id,
        name=file.filename or dest.name,
        rtsp_url=str(dest),
        role="upload",
    )
    db.add(camera)
    db.commit()
    return {
        "camera_id": str(camera.id),
        "name": camera.name,
        "duration_s": round(duration_s, 1),
        "fps": round(fps, 1),
    }


def probe_video(path: Path) -> tuple[float, float]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValidationError("That file is not a readable video.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    cap.release()
    duration_s = frames / fps if fps else 0.0
    if duration_s < 1.0:
        raise ValidationError("The video is empty or shorter than a second.")
    return duration_s, fps


def save_upload(file: UploadFile) -> tuple[Path, float, float]:
    suffix = Path(file.filename or "video.mp4").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValidationError(f"Unsupported file type {suffix!r}.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with dest.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            out.write(chunk)

    try:
        duration_s, fps = probe_video(dest)
    except ValidationError:
        dest.unlink(missing_ok=True)
        raise
    return dest, duration_s, fps


@router.post("/videos/{camera_id}/analyze", dependencies=[Depends(require_admin)])
def start_analysis(camera_id: uuid.UUID, db: DbDep, site: SiteDep, body: AnalyzeIn | None = None):
    camera = site_camera(db, site, camera_id)
    if camera.role not in ("upload", "cctv"):
        raise ValidationError("This source cannot be analyzed.")

    anchor_end = body.ends_at if body else None
    if anchor_end is not None and anchor_end.tzinfo is None:
        anchor_end = anchor_end.replace(tzinfo=ZoneInfo(site.timezone))

    job = submit_analysis(db, camera, anchor_end=anchor_end)
    return {"status": job.state, "position": jobs.queue_position(db, camera.id)}


@router.get("/videos/{camera_id}/processed")
def processed_video(camera_id: uuid.UUID, db: DbDep, site: SiteDep):
    camera = site_camera(db, site, camera_id)
    key = f"processed/{camera.id}.mp4"
    if not storage.object_exists(key):
        raise NotFoundError("The processed video is not ready yet.")
    return {"url": storage.presign_get(key)}


@router.get("/videos/{camera_id}/heatmap")
def traffic_heatmap(camera_id: uuid.UUID, db: DbDep, site: SiteDep):
    camera = site_camera(db, site, camera_id)
    key = f"heatmaps/{camera.id}.jpg"
    if not storage.object_exists(key):
        raise NotFoundError("The heatmap is not ready yet.")
    return {"url": storage.presign_get(key)}


@router.get("/videos/{camera_id}/status")
def analysis_status(camera_id: uuid.UUID, db: DbDep, site: SiteDep):
    site_camera(db, site, camera_id)
    job = jobs.get(db, camera_id)
    if job is None:
        return {"state": "idle", "progress": 0.0, "events_written": 0, "error": None}
    return {
        "state": job.state,
        "progress": job.progress,
        "events_written": job.events_written,
        "error": job.error,
        "position": jobs.queue_position(db, camera_id),
    }
