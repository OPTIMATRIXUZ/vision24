import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Camera, Zone
from app.routers import sources as sources_router

pytestmark = [pytest.mark.db]


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sources_router, "UPLOAD_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def demo_file(upload_dir):
    import cv2
    import numpy as np

    path = upload_dir / sources_router.DEMO_SOURCE
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (32, 32))
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    for _ in range(40):
        writer.write(frame)
    writer.release()
    assert path.exists()
    return path


@pytest.fixture
def submitted(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sources_router, "submit_analysis", lambda db, camera, **kw: calls.append(camera.id)
    )
    return calls


class TestDemoSource:
    def test_404_when_the_demo_file_is_not_installed(self, client, admin_headers, site, upload_dir):
        res = client.post("/api/sources/demo", headers=admin_headers)
        assert res.status_code == 404
        assert "not installed" in res.json()["error"]["message"]

    def test_creates_a_source_from_a_copy_and_starts_analysis(
        self, client, admin_headers, site, db, demo_file, submitted
    ):
        res = client.post("/api/sources/demo", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["name"] == sources_router.DEMO_SOURCE_NAME
        assert body["duration_s"] >= 1.0
        assert body["zone_id"] is not None

        camera = db.get(Camera, uuid.UUID(body["camera_id"]))
        assert camera is not None
        assert camera.role == "upload"
        registered = Path(camera.rtsp_url)
        assert registered.exists()
        assert registered.name != sources_router.DEMO_SOURCE
        assert demo_file.exists()

        zones = db.scalars(select(Zone).where(Zone.camera_id == camera.id)).all()
        assert {z.kind for z in zones} == {"entrance", "checkout_area"}
        checkout = next(z for z in zones if z.kind == "checkout_area")
        assert checkout.record_clips
        assert len(checkout.polygon) == 8

        assert submitted == [camera.id]

    def test_a_second_call_reuses_the_source(
        self, client, admin_headers, site, demo_file, submitted, upload_dir
    ):
        first = client.post("/api/sources/demo", headers=admin_headers).json()
        second = client.post("/api/sources/demo", headers=admin_headers).json()
        assert second["camera_id"] == first["camera_id"]
        copies = [p for p in upload_dir.iterdir() if p.name != sources_router.DEMO_SOURCE]
        assert len(copies) == 1
