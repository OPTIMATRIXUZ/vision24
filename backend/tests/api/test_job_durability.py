import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import AnalysisJob
from app.services import jobs

pytestmark = [pytest.mark.db]


def make_job(db, camera, *, state="running", runtime_id=None, heartbeat=None, kind="analyze"):
    job = AnalysisJob(
        camera_id=camera.id,
        kind=kind,
        state=state,
        runtime_id=runtime_id or jobs.RUNTIME_ID,
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC) if state != "queued" else None,
        heartbeat_at=heartbeat,
    )
    db.add(job)
    db.flush()
    return job


class TestDurability:
    def test_a_job_outlives_the_request_that_made_it(self, db, camera):
        jobs.submit(db, camera.id, "analyze", lambda handle: None)

        db.expire_all()
        found = jobs.get(db, camera.id)

        assert found is not None
        assert found.state == "done"
        assert found.runtime_id == jobs.RUNTIME_ID

    def test_a_failing_job_records_why(self, db, camera):
        def explode(handle):
            raise RuntimeError("ffmpeg went missing")

        jobs.submit(db, camera.id, "analyze", explode)

        job = jobs.get(db, camera.id)
        assert job.state == "error"
        assert "ffmpeg went missing" in job.error
        assert job.finished_at is not None

    def test_a_camera_keeps_its_history(self, db, camera):
        jobs.submit(db, camera.id, "analyze", lambda h: None)
        jobs.submit(db, camera.id, "analyze", lambda h: None)

        from sqlalchemy import func, select

        count = db.scalar(
            select(func.count()).select_from(AnalysisJob).where(AnalysisJob.camera_id == camera.id)
        )
        assert count == 2

    def test_get_returns_the_most_recent_run(self, db, camera):
        old = make_job(db, camera, state="error")
        old.queued_at = datetime.now(UTC) - timedelta(hours=1)
        old.error = "the old one"
        db.flush()
        make_job(db, camera, state="done")

        assert jobs.get(db, camera.id).state == "done"

    def test_a_second_job_for_a_busy_camera_is_refused(self, db, camera):
        from app.errors import JobBusyError

        make_job(db, camera, state="running")

        with pytest.raises(JobBusyError):
            jobs.submit(db, camera.id, "analyze", lambda h: None)

    def test_a_finished_job_does_not_block_the_next_one(self, db, camera):
        make_job(db, camera, state="done")

        job = jobs.submit(db, camera.id, "analyze", lambda h: None)

        assert job is not None


class TestOrphanReconciliation:
    def test_a_job_from_a_dead_process_is_failed(self, db, camera):
        make_job(db, camera, state="running", runtime_id=uuid.uuid4().hex)

        assert jobs.reconcile_orphans() == 1

        db.expire_all()
        job = jobs.get(db, camera.id)
        assert job.state == "error"
        assert "restarted" in job.error
        assert job.finished_at is not None

    def test_reconciliation_unblocks_the_camera(self, db, camera):
        make_job(db, camera, state="running", runtime_id=uuid.uuid4().hex)
        assert jobs.is_active(db, camera.id) is True

        jobs.reconcile_orphans()
        db.expire_all()

        assert jobs.is_active(db, camera.id) is False

    def test_a_queued_job_from_a_dead_process_is_also_failed(self, db, camera):
        make_job(db, camera, state="queued", runtime_id=uuid.uuid4().hex)

        assert jobs.reconcile_orphans() == 1

    def test_this_process_own_jobs_are_left_alone(self, db, camera):
        make_job(db, camera, state="running", runtime_id=jobs.RUNTIME_ID)

        assert jobs.reconcile_orphans() == 0

        db.expire_all()
        assert jobs.get(db, camera.id).state == "running"

    def test_a_foreign_job_with_a_fresh_heartbeat_is_left_alone(self, db, camera):
        make_job(
            db,
            camera,
            state="running",
            runtime_id=uuid.uuid4().hex,
            heartbeat=datetime.now(UTC),
        )

        assert jobs.reconcile_orphans() == 0

    def test_a_foreign_job_with_a_stale_heartbeat_is_failed(self, db, camera):
        make_job(
            db,
            camera,
            state="running",
            runtime_id=uuid.uuid4().hex,
            heartbeat=datetime.now(UTC) - jobs.STALE_AFTER - timedelta(minutes=1),
        )

        assert jobs.reconcile_orphans() == 1

    def test_terminal_jobs_are_never_touched(self, db, camera, make_camera, site):
        other = make_camera(site, name="other")
        make_job(db, camera, state="done", runtime_id=uuid.uuid4().hex)
        make_job(db, other, state="error", runtime_id=uuid.uuid4().hex)

        assert jobs.reconcile_orphans() == 0


class TestProgressThrottle:
    def test_progress_writes_are_throttled(self, db, camera, monkeypatch):
        job = make_job(db, camera, state="running")
        handle = jobs.JobHandle(job.id)

        writes = []
        real_update = jobs._update
        monkeypatch.setattr(
            jobs, "_update", lambda db_, jid, **f: (writes.append(f), real_update(db_, jid, **f))[1]
        )

        for i in range(500):
            handle.set_progress(0.30 + i * 0.00001, i)

        assert len(writes) == 1, f"{len(writes)} writes for 500 frames"

    def test_a_large_jump_is_written_immediately(self, db, camera, monkeypatch):
        job = make_job(db, camera, state="running")
        handle = jobs.JobHandle(job.id)

        writes = []
        real_update = jobs._update
        monkeypatch.setattr(
            jobs, "_update", lambda db_, jid, **f: (writes.append(f), real_update(db_, jid, **f))[1]
        )

        handle.set_progress(0.10, 1)
        handle.set_progress(0.90, 99)

        assert len(writes) == 2

    def test_progress_reaches_the_row(self, db, camera):
        job = make_job(db, camera, state="running")

        jobs.JobHandle(job.id).set_progress(0.42, 17)

        db.expire_all()
        refreshed = jobs.get(db, camera.id)
        assert refreshed.progress == pytest.approx(0.42)
        assert refreshed.events_written == 17
        assert refreshed.heartbeat_at is not None

    def test_updating_a_deleted_job_is_not_an_error(self, db, camera):
        job = make_job(db, camera, state="running")
        job_id = job.id
        db.delete(job)
        db.commit()

        jobs.JobHandle(job_id).set_progress(0.5, 1)


class TestStatusRoute:
    def test_status_reports_a_finished_job(self, client, owner_headers, site, camera, db):
        jobs.submit(db, camera.id, "analyze", lambda h: None)

        res = client.get(f"/api/videos/{camera.id}/status", headers=owner_headers)

        assert res.status_code == 200
        assert res.json()["state"] == "done"

    def test_status_surfaces_an_interrupted_job(self, client, owner_headers, site, camera, db):
        make_job(db, camera, state="running", runtime_id=uuid.uuid4().hex)
        jobs.reconcile_orphans()

        res = client.get(f"/api/videos/{camera.id}/status", headers=owner_headers)

        body = res.json()
        assert body["state"] == "error"
        assert "restarted" in body["error"]

    def test_status_is_idle_when_nothing_ever_ran(self, client, owner_headers, site, camera):
        res = client.get(f"/api/videos/{camera.id}/status", headers=owner_headers)

        assert res.json()["state"] == "idle"


def test_deleting_a_source_removes_its_job_rows(client, owner_headers, site, camera, db):
    jobs.submit(db, camera.id, "analyze", lambda h: None)
    assert jobs.get(db, camera.id) is not None

    res = client.delete(f"/api/sources/{camera.id}", headers=owner_headers)

    assert res.status_code == 200
    assert jobs.get(db, camera.id) is None
