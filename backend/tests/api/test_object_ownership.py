import pytest

from app.errors import NotFoundError
from app.services import analytics

pytestmark = [pytest.mark.db]


POLYGON = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


class TestZoneCameraOwnership:
    def _body(self, camera_id):
        return {
            "camera_id": str(camera_id),
            "name": "Injected",
            "kind": "entrance",
            "polygon": POLYGON,
            "record_clips": False,
            "privacy_mask": False,
        }

    def test_cannot_create_a_zone_on_another_tenants_camera(
        self, client, admin_headers, db, site, other_camera
    ):
        res = client.post("/api/zones", headers=admin_headers, json=self._body(other_camera.id))
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "not_found"

    def test_cannot_repoint_an_existing_zone_at_another_tenants_camera(
        self, client, admin_headers, db, site, zone, other_camera
    ):
        res = client.put(
            f"/api/zones/{zone.id}", headers=admin_headers, json=self._body(other_camera.id)
        )
        assert res.status_code == 404

        db.refresh(zone)
        assert zone.camera_id != other_camera.id

    def test_a_zone_never_ends_up_pointing_outside_its_own_site(
        self, client, admin_headers, db, site, zone, camera, other_camera
    ):
        from app.models import Camera, Zone

        client.put(f"/api/zones/{zone.id}", headers=admin_headers, json=self._body(other_camera.id))
        db.expire_all()

        violations = (
            db.query(Zone.id)
            .join(Camera, Zone.camera_id == Camera.id)
            .filter(Zone.site_id != Camera.site_id)
            .all()
        )
        assert not violations, f"zones pointing outside their site: {violations}"

    def test_a_camera_in_the_same_site_is_still_accepted(
        self, client, admin_headers, db, site, camera
    ):
        res = client.post("/api/zones", headers=admin_headers, json=self._body(camera.id))
        assert res.status_code == 200
        assert res.json()["camera_id"] == str(camera.id)


class TestClipLookupsAreSiteScoped:

    def test_clips_for_a_foreign_event_are_not_returned(
        self, db, site, other_site, make_camera, make_event, make_clip
    ):
        their_camera = make_camera(other_site, name="Theirs")
        their_event = make_event(their_camera)
        make_clip(their_event)

        found = analytics.get_clips_for_events(db, site, [their_event.id], limit=10)
        assert found == []

    def test_snapshots_for_a_foreign_event_are_not_returned(
        self, db, site, other_site, make_camera, make_event, make_clip
    ):
        their_camera = make_camera(other_site, name="Theirs")
        their_event = make_event(their_camera)
        make_clip(their_event)

        found = analytics.snapshot_keys_for_events(db, site, [their_event.id])
        assert found == {}

    def test_a_mixed_id_list_returns_only_our_own(
        self, db, site, other_site, camera, make_camera, make_event, make_clip
    ):
        mine = make_event(camera)
        make_clip(mine)
        their_camera = make_camera(other_site, name="Theirs")
        theirs = make_event(their_camera)
        make_clip(theirs)

        clips = analytics.get_clips_for_events(db, site, [mine.id, theirs.id], limit=10)
        assert [c.event_id for c in clips] == [mine.id]

        snaps = analytics.snapshot_keys_for_events(db, site, [mine.id, theirs.id])
        assert list(snaps) == [mine.id]

    def test_our_own_clips_are_still_found(self, db, site, camera, make_event, make_clip):
        mine = make_event(camera)
        clip = make_clip(mine)

        clips = analytics.get_clips_for_events(db, site, [mine.id], limit=10)
        assert [c.id for c in clips] == [clip.id]


class TestReplayGating:
    def test_replay_is_refused_outside_development(
        self, client, admin_headers, tenant, monkeypatch
    ):
        from app.config import settings

        monkeypatch.setattr(settings, "environment", "prod")
        res = client.post("/api/live/replay", headers=admin_headers)
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "not_found"

    def test_replay_still_works_in_development(self, client, admin_headers, site, monkeypatch):
        from app.config import settings
        from app.services import replay

        monkeypatch.setattr(settings, "environment", "dev")
        monkeypatch.setattr(replay, "start_replay", lambda stream="cam1": {"status": "started"})
        res = client.post("/api/live/replay", headers=admin_headers)
        assert res.status_code == 200


def test_resolve_site_rejects_a_foreign_camera_helper(db, site, other_camera):
    from app.scoping import site_camera

    with pytest.raises(NotFoundError):
        site_camera(db, site, other_camera.id)
