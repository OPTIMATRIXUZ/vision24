import pytest

pytestmark = [pytest.mark.db]


@pytest.fixture
def no_ffmpeg(monkeypatch):
    started: list[str] = []

    from app.services import replay

    monkeypatch.setattr(
        replay, "start_replay", lambda stream="cam1": started.append(stream) or {"playing": True}
    )
    return started


class TestStreamName:
    def test_basename_of_the_relay_url(self):
        from app.routers.live import stream_name

        assert stream_name("rtsp://127.0.0.1:8554/cam2") == "cam2"
        assert stream_name("rtsp://relay:8554/cam1/") == "cam1"

    def test_unusable_urls_are_rejected(self):
        from app.errors import ValidationError
        from app.routers.live import stream_name

        with pytest.raises(ValidationError):
            stream_name("rtsp://relay:8554/")
        with pytest.raises(ValidationError):
            stream_name("rtsp://relay:8554/with spaces")


class TestReplayTargeting:
    def test_default_stays_cam1(self, client, admin_headers, site, no_ffmpeg):
        res = client.post("/api/live/replay", headers=admin_headers, json={})
        assert res.status_code == 200
        assert no_ffmpeg == ["cam1"]

    def test_camera_picks_its_stream(self, client, admin_headers, site, make_camera, db, no_ffmpeg):
        cam = make_camera(site, name="Cam 2", role="cctv")
        cam.rtsp_url = "rtsp://127.0.0.1:8554/cam2"
        db.flush()
        res = client.post(
            "/api/live/replay", headers=admin_headers, json={"camera_id": str(cam.id)}
        )
        assert res.status_code == 200
        assert no_ffmpeg == ["cam2"]

    def test_upload_camera_is_refused(self, client, admin_headers, site, camera, no_ffmpeg):
        res = client.post(
            "/api/live/replay", headers=admin_headers, json={"camera_id": str(camera.id)}
        )
        assert res.status_code == 400
        assert no_ffmpeg == []

    def test_another_tenants_camera_is_404(
        self, client, admin_headers, site, other_camera, no_ffmpeg
    ):
        res = client.post(
            "/api/live/replay", headers=admin_headers, json={"camera_id": str(other_camera.id)}
        )
        assert res.status_code == 404
        assert no_ffmpeg == []
