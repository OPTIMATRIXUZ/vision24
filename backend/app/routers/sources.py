import base64
import contextlib
import logging
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, UploadFile
from sqlalchemy import delete, func, select

from app import storage
from app.deps import DbDep, PrincipalDep, SiteDep, require_admin
from app.errors import ConflictError, JobBusyError, NotFoundError, ValidationError, Vision24Error
from app.models import Alert, AlertRule, Camera, Clip, Embedding, Event, Zone
from app.routers.videos import UPLOAD_DIR, probe_video, save_upload, submit_analysis
from app.routers.zones import FULL_FRAME, create_zone_with_rule
from app.schemas import (
    CaptureIn,
    CctvIn,
    CctvTestIn,
    SourceJobOut,
    SourceOut,
    SourceZoneOut,
    UploadSourceOut,
)
from app.scoping import site_camera
from app.services import capture as capture_service
from app.services import jobs
from app.services.ai import chat as chat_service
from app.services.ai import report as report_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["sources"])

ZONE_KINDS = (
    "entrance",
    "checkout_area",
    "store_room",
    "dining",
    "truck",
    "delivery_door",
    "custom",
)


def _redact_rtsp(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.password or parts.username:
            host = parts.hostname or ""
            if parts.port:
                host += f":{parts.port}"
            netloc = f"{parts.username or 'user'}:***@{host}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
        return url
    except ValueError:
        return "rtsp://<camera>"


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: DbDep, site: SiteDep):
    cameras = db.scalars(
        select(Camera)
        .where(Camera.site_id == site.id, Camera.role.in_(("upload", "cctv")))
        .order_by(Camera.name)
    ).all()
    if not cameras:
        return []
    camera_ids = [c.id for c in cameras]

    zones = db.scalars(select(Zone).where(Zone.camera_id.in_(camera_ids))).all()
    zones_by_cam: dict = {}
    for z in zones:
        zones_by_cam.setdefault(z.camera_id, []).append(z)

    stats = {
        r.camera_id: r
        for r in db.execute(
            select(
                Event.camera_id,
                func.count(Event.id).label("events"),
                func.max(Event.ts_start).label("last_ts"),
                func.count(Event.id).filter(Event.type == "entry").label("entries"),
            )
            .where(Event.camera_id.in_(camera_ids))
            .group_by(Event.camera_id)
        ).all()
    }

    out = []
    for cam in cameras:
        stat = stats.get(cam.id)
        job = jobs.get(db, cam.id)
        out.append(
            SourceOut(
                camera_id=cam.id,
                name=cam.name,
                source_type=cam.role,
                rtsp_url=_redact_rtsp(cam.rtsp_url) if cam.role == "cctv" else None,
                zones=[
                    SourceZoneOut(id=z.id, name=z.name, kind=z.kind)
                    for z in zones_by_cam.get(cam.id, [])
                ],
                last_analyzed=stat.last_ts if stat else None,
                events_count=stat.events if stat else 0,
                entries_count=stat.entries if stat else 0,
                has_processed=storage.object_exists(f"processed/{cam.id}.mp4"),
                job=SourceJobOut.model_validate(job, from_attributes=True) if job else None,
            )
        )
    return out


@router.post(
    "/sources/upload", response_model=UploadSourceOut, dependencies=[Depends(require_admin)]
)
def add_upload_source(
    db: DbDep,
    site: SiteDep,
    file: UploadFile,
    name: str = Form(min_length=1, max_length=100),
    kind: str = Form("entrance"),
    auto_zone: bool = Form(True),
):
    if kind not in ZONE_KINDS:
        raise ValidationError(f"Unknown zone kind {kind!r}.")
    dest, duration_s, fps = save_upload(file)

    camera = Camera(site_id=site.id, name=name, rtsp_url=str(dest), role="upload")
    db.add(camera)
    db.flush()
    zone = None
    if auto_zone:
        zone = create_zone_with_rule(
            db,
            site_id=site.id,
            camera_id=camera.id,
            name=name,
            kind=kind,
            polygon=FULL_FRAME,
            record_clips=True,
        )
    db.commit()
    return UploadSourceOut(
        camera_id=camera.id,
        zone_id=zone.id if zone else None,
        name=camera.name,
        duration_s=round(duration_s, 1),
        fps=round(fps, 1),
    )


DEMO_SOURCE = "demo.mp4"
DEMO_SOURCE_NAME = "Demo video"

# The demo footage is a real checkout camera. The checkout polygon hugs the
# counter's customer side; a wider strip would sit on the cashier's feet and
# collapse the whole recording into one endless "visit".
DEMO_ZONES = [
    {
        "name": "Entrance",
        "kind": "entrance",
        "polygon": FULL_FRAME,
        "record_clips": False,
    },
    {
        "name": "Checkout",
        "kind": "checkout_area",
        "polygon": [
            [0.13, 0.55],
            [0.30, 0.48],
            [0.45, 0.35],
            [0.56, 0.21],
            [0.48, 0.12],
            [0.38, 0.23],
            [0.24, 0.34],
            [0.10, 0.42],
        ],
        "record_clips": True,
    },
]


@router.post("/sources/demo", response_model=UploadSourceOut, dependencies=[Depends(require_admin)])
def add_demo_source(db: DbDep, site: SiteDep):
    master = UPLOAD_DIR / DEMO_SOURCE
    if not master.exists():
        raise NotFoundError("The demo video is not installed on this server.")

    existing = db.scalars(
        select(Camera).where(
            Camera.site_id == site.id,
            Camera.name == DEMO_SOURCE_NAME,
            Camera.role == "upload",
        )
    ).first()
    if existing is not None and Path(existing.rtsp_url).exists():
        duration_s, fps = probe_video(Path(existing.rtsp_url))
        with contextlib.suppress(JobBusyError):
            submit_analysis(db, existing)
        zone = db.scalars(select(Zone).where(Zone.camera_id == existing.id)).first()
        return UploadSourceOut(
            camera_id=existing.id,
            zone_id=zone.id if zone else None,
            name=existing.name,
            duration_s=round(duration_s, 1),
            fps=round(fps, 1),
        )

    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}.mp4"
    shutil.copy2(master, dest)
    try:
        duration_s, fps = probe_video(dest)
    except ValidationError:
        dest.unlink(missing_ok=True)
        raise

    camera = Camera(site_id=site.id, name=DEMO_SOURCE_NAME, rtsp_url=str(dest), role="upload")
    db.add(camera)
    db.flush()
    zone = None
    for spec in DEMO_ZONES:
        zone = create_zone_with_rule(
            db,
            site_id=site.id,
            camera_id=camera.id,
            name=spec["name"],
            kind=spec["kind"],
            polygon=spec["polygon"],
            record_clips=spec["record_clips"],
        )
    db.commit()
    submit_analysis(db, camera)
    return UploadSourceOut(
        camera_id=camera.id,
        zone_id=zone.id if zone else None,
        name=camera.name,
        duration_s=round(duration_s, 1),
        fps=round(fps, 1),
    )


@router.post(
    "/sources/{camera_id}/reupload",
    response_model=UploadSourceOut,
    dependencies=[Depends(require_admin)],
)
def reupload_source(camera_id: uuid.UUID, file: UploadFile, db: DbDep, site: SiteDep):
    camera = site_camera(db, site, camera_id)
    if camera.role != "upload":
        raise ValidationError("Only uploaded sources can be re-uploaded.")
    if jobs.is_active(db, camera.id):
        raise JobBusyError()

    old_path = Path(camera.rtsp_url)
    dest, duration_s, fps = save_upload(file)
    camera.rtsp_url = str(dest)
    db.commit()

    if old_path != dest:
        old_path.unlink(missing_ok=True)
    storage.remove_object(f"processed/{camera.id}.mp4")
    storage.remove_object(f"heatmaps/{camera.id}.jpg")
    return UploadSourceOut(
        camera_id=camera.id,
        name=camera.name,
        duration_s=round(duration_s, 1),
        fps=round(fps, 1),
    )


@router.post("/sources/cctv", response_model=UploadSourceOut, dependencies=[Depends(require_admin)])
def add_cctv_source(body: CctvIn, db: DbDep, site: SiteDep):
    camera = Camera(site_id=site.id, name=body.name, rtsp_url=body.rtsp_url, role="cctv")
    db.add(camera)
    db.flush()
    zone = None
    if body.auto_zone:
        zone = create_zone_with_rule(
            db,
            site_id=site.id,
            camera_id=camera.id,
            name=body.name,
            kind=body.kind,
            polygon=FULL_FRAME,
            record_clips=True,
        )
    db.commit()
    return UploadSourceOut(
        camera_id=camera.id,
        zone_id=zone.id if zone else None,
        name=camera.name,
        duration_s=0.0,
        fps=0.0,
    )


@router.post("/sources/cctv/test")
def test_cctv(body: CctvTestIn, principal: PrincipalDep):
    try:
        jpeg = capture_service.probe_snapshot(body.rtsp_url)
    except capture_service.CaptureError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "snapshot_b64": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
    }


@router.post("/sources/{camera_id}/capture", dependencies=[Depends(require_admin)])
def capture_and_analyze(camera_id: uuid.UUID, body: CaptureIn, db: DbDep, site: SiteDep):
    camera = site_camera(db, site, camera_id)
    if camera.role != "cctv":
        raise ValidationError("This is not a CCTV source.")
    cam_id = camera.id
    rtsp_url = camera.rtsp_url
    duration_s = body.duration_s

    from app.db import SessionLocal

    def exists_check() -> bool:
        with SessionLocal() as check_db:
            return check_db.get(Camera, cam_id) is not None

    def run(handle: jobs.JobHandle) -> None:
        from worker.batch import analyze_video

        handle.set_state("capturing")
        dest = capture_service.capture_path(cam_id)
        capture_service.capture_segment(rtsp_url, dest, duration_s, camera_id=cam_id)
        handle.set_state("running")
        result = analyze_video(cam_id, str(dest), progress_cb=handle.set_progress)
        log.info("Capture+analysis of %s finished: %s", cam_id, result)

    job = jobs.submit(db, cam_id, "capture", run, exists_check)
    return {"status": job.state, "position": jobs.queue_position(db, cam_id)}


@router.post("/sources/{camera_id}/capture/stop", dependencies=[Depends(require_admin)])
def stop_capture(camera_id: uuid.UUID, db: DbDep, site: SiteDep):
    camera = site_camera(db, site, camera_id)
    job = jobs.get(db, camera.id)
    if job is None or job.state != "capturing":
        raise ConflictError("No capture is in progress for this source.")
    if not capture_service.request_stop(camera.id):
        raise ConflictError("The capture is not running — it may have just finished.")
    return {"status": "stopping"}


@router.post("/sources/analyze-all", dependencies=[Depends(require_admin)])
def analyze_all(db: DbDep, site: SiteDep):
    cameras = db.scalars(
        select(Camera).where(Camera.site_id == site.id, Camera.role.in_(("upload", "cctv")))
    ).all()
    queued, skipped = [], []
    for camera in cameras:
        try:
            submit_analysis(db, camera)
            queued.append(str(camera.id))
        except Vision24Error as exc:
            skipped.append({"camera_id": str(camera.id), "reason": exc.message})
    return {"queued": queued, "skipped": skipped}


@router.delete("/sources/{camera_id}", dependencies=[Depends(require_admin)])
def delete_source(camera_id: uuid.UUID, db: DbDep, site: SiteDep, principal: PrincipalDep):
    camera = site_camera(db, site, camera_id)
    if camera.role not in ("upload", "cctv"):
        raise ValidationError("This source cannot be deleted.")
    if jobs.is_active(db, camera.id):
        raise JobBusyError()
    jobs.drop(db, camera.id)

    site_id = camera.site_id
    camera_role = camera.role
    camera_path = camera.rtsp_url

    event_ids = select(Event.id).where(Event.camera_id == camera.id)
    zone_ids = select(Zone.id).where(Zone.camera_id == camera.id)
    rule_ids = select(AlertRule.id).where(AlertRule.zone_id.in_(zone_ids))

    for clip in db.scalars(select(Clip).where(Clip.event_id.in_(event_ids))):
        if clip.storage_key:
            storage.remove_object(clip.storage_key)
        if clip.snapshot_key:
            storage.remove_object(clip.snapshot_key)
    for key in db.scalars(
        select(Event.attributes["crop_key"].astext).where(
            Event.camera_id == camera.id, Event.type == "delivery_trip"
        )
    ):
        if key:
            storage.remove_object(key)
    storage.remove_object(f"processed/{camera.id}.mp4")
    storage.remove_object(f"heatmaps/{camera.id}.jpg")

    db.execute(delete(Alert).where(Alert.event_id.in_(event_ids)))
    db.execute(delete(Alert).where(Alert.rule_id.in_(rule_ids)))
    db.execute(delete(Embedding).where(Embedding.event_id.in_(event_ids)))
    db.execute(delete(Clip).where(Clip.event_id.in_(event_ids)))
    db.execute(delete(Event).where(Event.camera_id == camera.id))
    db.execute(delete(AlertRule).where(AlertRule.zone_id.in_(zone_ids)))
    db.execute(delete(Zone).where(Zone.camera_id == camera.id))
    db.delete(camera)
    db.commit()

    if camera_role == "upload":
        Path(camera_path).unlink(missing_ok=True)
    capture_service.capture_path(camera_id).unlink(missing_ok=True)

    chat_service.clear_sessions(principal.tenant_id)
    report_service.clear_cache(site_id)
    return {"deleted": str(camera_id)}
