import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.errors import JobBusyError
from app.models import JOB_TERMINAL, AnalysisJob

log = logging.getLogger(__name__)

RUNTIME_ID = uuid.uuid4().hex

PROGRESS_INTERVAL_S = 2.0
PROGRESS_STEP = 0.05

STALE_AFTER = timedelta(minutes=5)


class JobHandle:

    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        self._last_write = 0.0
        self._last_fraction = -1.0

    def set_state(self, state: str) -> None:
        with SessionLocal() as db:
            _update(db, self.job_id, state=state)

    def set_progress(self, fraction: float, events: int) -> None:
        now = time.monotonic()
        big_jump = abs(fraction - self._last_fraction) >= PROGRESS_STEP
        if not big_jump and now - self._last_write < PROGRESS_INTERVAL_S:
            return
        self._last_write = now
        self._last_fraction = fraction
        with SessionLocal() as db:
            _update(
                db,
                self.job_id,
                progress=round(fraction, 3),
                events_written=events,
                heartbeat_at=datetime.now(UTC),
            )


def _update(db: Session, job_id: uuid.UUID, **fields) -> None:
    job = db.get(AnalysisJob, job_id)
    if job is None:
        return
    for key, value in fields.items():
        setattr(job, key, value)
    db.commit()


_pending: dict[uuid.UUID, tuple[Callable, Callable | None]] = {}
_queue: "queue.Queue[uuid.UUID]" = queue.Queue()
_lock = threading.Lock()
_thread: threading.Thread | None = None

_inline = False


def set_inline_execution(value: bool) -> None:
    global _inline
    _inline = value


def submit(
    db: Session,
    camera_id: uuid.UUID,
    kind: str,
    fn: Callable[[JobHandle], None],
    exists_check: Callable[[], bool] | None = None,
) -> AnalysisJob:
    if _live_job(db, [camera_id]) is not None:
        raise JobBusyError("This source already has a queued or running job.")

    job = AnalysisJob(
        camera_id=camera_id,
        kind=kind,
        state="queued",
        runtime_id=RUNTIME_ID,
        queued_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()

    with _lock:
        _pending[job.id] = (fn, exists_check)

    if _inline:
        _execute(job.id)
        db.refresh(job)
        return job

    _queue.put(job.id)
    _ensure_worker()
    return job


def get(db: Session, camera_id: uuid.UUID) -> AnalysisJob | None:
    return db.scalars(
        select(AnalysisJob)
        .where(AnalysisJob.camera_id == camera_id)
        .order_by(AnalysisJob.queued_at.desc(), AnalysisJob.id.desc())
        .limit(1)
    ).first()


def queue_position(db: Session, camera_id: uuid.UUID) -> int:
    mine = get(db, camera_id)
    if mine is None or mine.state != "queued":
        return 0
    ahead = db.scalar(
        select(func.count())
        .select_from(AnalysisJob)
        .where(AnalysisJob.state == "queued", AnalysisJob.queued_at < mine.queued_at)
    )
    return 1 + (ahead or 0)


def _live_job(db: Session, camera_ids: Iterable[uuid.UUID] | None) -> AnalysisJob | None:
    stmt = select(AnalysisJob).where(AnalysisJob.state.not_in(JOB_TERMINAL))
    if camera_ids is not None:
        ids = list(camera_ids)
        if not ids:
            return None
        stmt = stmt.where(AnalysisJob.camera_id.in_(ids))
    return db.scalars(stmt.limit(1)).first()


def is_active(db: Session, camera_id: uuid.UUID) -> bool:
    return _live_job(db, [camera_id]) is not None


def any_active(db: Session, camera_ids: Iterable[uuid.UUID] | None = None) -> bool:
    return _live_job(db, camera_ids) is not None


def drop(db: Session, camera_id: uuid.UUID) -> None:
    db.execute(
        delete(AnalysisJob).where(
            AnalysisJob.camera_id == camera_id, AnalysisJob.state.in_(JOB_TERMINAL)
        )
    )
    db.commit()


def clear(db: Session, camera_ids: Iterable[uuid.UUID] | None = None) -> None:
    stmt = delete(AnalysisJob)
    if camera_ids is not None:
        ids = list(camera_ids)
        if not ids:
            return
        stmt = stmt.where(AnalysisJob.camera_id.in_(ids))
    db.execute(stmt)
    db.commit()
    if camera_ids is None:
        with _lock:
            _pending.clear()


def reconcile_orphans() -> int:
    cutoff = datetime.now(UTC) - STALE_AFTER
    with SessionLocal() as db:
        orphans = list(
            db.scalars(
                select(AnalysisJob).where(
                    AnalysisJob.state.not_in(JOB_TERMINAL),
                    AnalysisJob.runtime_id != RUNTIME_ID,
                    (AnalysisJob.heartbeat_at.is_(None)) | (AnalysisJob.heartbeat_at < cutoff),
                )
            )
        )
        for job in orphans:
            job.state = "error"
            job.finished_at = datetime.now(UTC)
            job.error = (
                "Interrupted — the server restarted while this job was running. "
                "Any events written before the restart were kept; run the analysis "
                "again to complete it."
            )
        if orphans:
            db.commit()
            log.warning(
                "Reconciled %d job(s) left behind by a previous run: %s",
                len(orphans),
                ", ".join(f"{j.kind}/{j.camera_id}" for j in orphans),
            )
    return len(orphans)


def _ensure_worker() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_worker_loop, daemon=True, name="job-worker")
        _thread.start()


def _worker_loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            _execute(job_id)
        except Exception:
            log.exception("Job executor raised for %s", job_id)


def _execute(job_id: uuid.UUID) -> None:
    with _lock:
        entry = _pending.pop(job_id, None)

    with SessionLocal() as db:
        job = db.get(AnalysisJob, job_id)
        if job is None or entry is None or job.state in JOB_TERMINAL:
            return
        fn, exists_check = entry
        job.state = "running"
        job.started_at = datetime.now(UTC)
        job.heartbeat_at = job.started_at
        db.commit()

    try:
        if exists_check is not None and not exists_check():
            _finish(job_id, "error", error="Source was deleted while queued.")
            return
        fn(JobHandle(job_id))
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        _finish(job_id, "error", error=str(exc))
        return
    _finish(job_id, "done", progress=1.0)


def _finish(job_id: uuid.UUID, state: str, **fields) -> None:
    with SessionLocal() as db:
        _update(db, job_id, state=state, finished_at=datetime.now(UTC), **fields)
