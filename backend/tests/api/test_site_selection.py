import uuid

import pytest

pytestmark = [pytest.mark.db]

COLLECTIONS = [
    ("/api/cameras", "id"),
    ("/api/zones", "id"),
    ("/api/sources", "camera_id"),
    ("/api/alert-rules", "id"),
]


@pytest.fixture
def two_sites(db, site, tenant_second_site, make_camera, make_zone, make_alert_rule):
    for s, name in ((site, "A"), (tenant_second_site, "B")):
        cam = make_camera(s, name=f"cam-{name}")
        zone = make_zone(s, cam, name=f"zone-{name}")
        make_alert_rule(zone)
    db.flush()
    return site, tenant_second_site


def ids(res, key: str) -> set:
    return {row[key] for row in res.json()}


class TestSiteSelector:
    def test_site_id_selects_a_different_site_of_the_same_tenant(
        self, client, admin_headers, two_sites
    ):
        first, second = two_sites

        res = client.get(f"/api/site?site_id={second.id}", headers=admin_headers)

        assert res.status_code == 200
        assert res.json()["id"] == str(second.id)
        assert res.json()["id"] != str(first.id)

    def test_every_collection_route_describes_the_selected_site(
        self, client, admin_headers, two_sites
    ):
        default = client.get("/api/site", headers=admin_headers).json()["id"]
        other = next(str(s.id) for s in two_sites if str(s.id) != default)

        for path, key in COLLECTIONS:
            here = client.get(path, headers=admin_headers)
            there = client.get(f"{path}?site_id={other}", headers=admin_headers)
            assert here.status_code == there.status_code == 200, path
            assert len(here.json()) == 1, f"{path} returned both sites' rows"
            assert len(there.json()) == 1, path
            assert ids(here, key).isdisjoint(ids(there, key)), (
                f"{path} returned the same row for two different sites"
            )

    def test_metrics_and_cameras_cannot_disagree(self, client, admin_headers, two_sites):
        _, second = two_sites
        q = f"?site_id={second.id}"

        cameras = client.get(f"/api/cameras{q}", headers=admin_headers).json()
        summary = client.get(f"/api/metrics/summary{q}", headers=admin_headers)
        site = client.get(f"/api/site{q}", headers=admin_headers).json()

        assert summary.status_code == 200
        assert site["id"] == str(second.id)
        assert [c["name"] for c in cameras] == ["cam-B"]

    def test_another_tenants_site_is_not_selectable(self, client, admin_headers, site, other_site):
        res = client.get(f"/api/site?site_id={other_site.id}", headers=admin_headers)

        assert res.status_code == 404

    def test_a_foreign_site_is_indistinguishable_from_a_nonexistent_one(
        self, client, admin_headers, site, other_site
    ):
        foreign = client.get(f"/api/site?site_id={other_site.id}", headers=admin_headers)
        absent = client.get(f"/api/site?site_id={uuid.uuid4()}", headers=admin_headers)

        assert foreign.status_code == absent.status_code == 404
        assert foreign.json()["error"]["message"] == absent.json()["error"]["message"]

    def test_a_malformed_site_id_is_a_validation_error(self, client, admin_headers, site):
        res = client.get("/api/site?site_id=not-a-uuid", headers=admin_headers)

        assert res.status_code == 422


class TestObjectsFollowTheSelectedSite:
    def test_a_camera_of_another_site_is_not_reachable(self, client, admin_headers, two_sites, db):
        first, second = two_sites
        from app.models import Camera

        cam_a = db.scalars(select_camera(Camera, first)).first()

        res = client.get(
            f"/api/videos/{cam_a.id}/status?site_id={second.id}", headers=admin_headers
        )

        assert res.status_code == 404

    def test_the_same_camera_is_reachable_with_its_own_site(
        self, client, admin_headers, two_sites, db
    ):
        first, _ = two_sites
        from app.models import Camera

        cam_a = db.scalars(select_camera(Camera, first)).first()

        res = client.get(f"/api/videos/{cam_a.id}/status?site_id={first.id}", headers=admin_headers)

        assert res.status_code == 200

    def test_a_zone_cannot_be_attached_to_another_sites_camera(
        self, client, admin_headers, two_sites, db
    ):
        first, second = two_sites
        from app.models import Camera

        cam_a = db.scalars(select_camera(Camera, first)).first()

        res = client.post(
            f"/api/zones?site_id={second.id}",
            headers=admin_headers,
            json={
                "camera_id": str(cam_a.id),
                "name": "z",
                "kind": "entrance",
                "polygon": [[0, 0], [1, 0], [1, 1]],
            },
        )

        assert res.status_code == 404

    def test_an_alert_rule_cannot_be_moved_onto_another_sites_zone(
        self, client, admin_headers, two_sites, db
    ):
        first, second = two_sites
        from app.models import AlertRule, Zone

        zone_a = db.scalars(select_zone(Zone, first)).first()
        zone_b = db.scalars(select_zone(Zone, second)).first()
        rule_b = db.scalars(select_rule(AlertRule, zone_b)).first()

        res = client.put(
            f"/api/alert-rules/{rule_b.id}?site_id={second.id}",
            headers=admin_headers,
            json={"zone_id": str(zone_a.id), "metric": "queue_len", "threshold": 9},
        )

        assert res.status_code == 404
        db.refresh(rule_b)
        assert rule_b.zone_id == zone_b.id, "the rule was moved to another site's zone"


class TestApiKeySitePin:
    def test_a_pinned_key_resolves_its_site_without_a_query(
        self, client, two_sites, tenant, make_api_key
    ):
        _, second = two_sites
        full, _key = make_api_key(tenant, site=second)

        res = client.get("/api/site", headers={"Authorization": f"Bearer {full}"})

        assert res.status_code == 200
        assert res.json()["id"] == str(second.id)

    def test_a_pinned_key_cannot_be_steered_to_another_site(
        self, client, two_sites, tenant, make_api_key
    ):
        first, second = two_sites
        full, _key = make_api_key(tenant, site=second)

        res = client.get(
            f"/api/site?site_id={first.id}", headers={"Authorization": f"Bearer {full}"}
        )

        assert res.status_code == 404

    def test_asking_for_the_pinned_site_explicitly_is_fine(
        self, client, two_sites, tenant, make_api_key
    ):
        _, second = two_sites
        full, _key = make_api_key(tenant, site=second)

        res = client.get(
            f"/api/site?site_id={second.id}", headers={"Authorization": f"Bearer {full}"}
        )

        assert res.status_code == 200
        assert res.json()["id"] == str(second.id)

    def test_an_unpinned_key_can_still_select(self, client, two_sites, tenant, make_api_key):
        _first, second = two_sites
        full, _key = make_api_key(tenant)

        res = client.get(
            f"/api/site?site_id={second.id}", headers={"Authorization": f"Bearer {full}"}
        )

        assert res.status_code == 200
        assert res.json()["id"] == str(second.id)


def test_reset_stays_tenant_wide(client, owner_headers, tenant, two_sites, db):
    from app.models import Camera

    first, second = two_sites

    res = client.post(
        f"/api/reset?site_id={first.id}", headers=owner_headers, json={"confirm": tenant.slug}
    )

    assert res.status_code == 200
    remaining = db.scalars(select_camera(Camera, first).union(select_camera(Camera, second))).all()
    assert remaining == [], "reset left another site's cameras behind"


def select_camera(Camera, site):
    from sqlalchemy import select

    return select(Camera).where(Camera.site_id == site.id)


def select_zone(Zone, site):
    from sqlalchemy import select

    return select(Zone).where(Zone.site_id == site.id)


def select_rule(AlertRule, zone):
    from sqlalchemy import select

    return select(AlertRule).where(AlertRule.zone_id == zone.id)


class TestSitesList:

    def test_lists_every_site_of_the_tenant(self, client, admin_headers, two_sites):
        res = client.get("/api/sites", headers=admin_headers)

        assert res.status_code == 200
        assert {s["id"] for s in res.json()} == {str(s.id) for s in two_sites}

    def test_another_tenants_sites_are_not_listed(self, client, admin_headers, site, other_site):
        res = client.get("/api/sites", headers=admin_headers)

        assert [s["id"] for s in res.json()] == [str(site.id)]

    def test_the_first_entry_is_the_one_a_request_gets_by_default(
        self, client, admin_headers, two_sites
    ):
        listed = client.get("/api/sites", headers=admin_headers).json()
        default = client.get("/api/site", headers=admin_headers).json()

        assert listed[0]["id"] == default["id"]

    def test_a_pinned_api_key_sees_only_its_own_site(self, client, two_sites, tenant, make_api_key):
        _first, second = two_sites
        full, _key = make_api_key(tenant, site=second)

        res = client.get("/api/sites", headers={"Authorization": f"Bearer {full}"})

        assert [s["id"] for s in res.json()] == [str(second.id)]

    def test_a_viewer_may_read_it(self, client, tenant, site, make_user, sign_in):
        viewer = make_user(tenant, role="viewer")
        headers = {"Authorization": f"Bearer {sign_in(viewer).json()['access_token']}"}
        client.cookies.clear()

        assert client.get("/api/sites", headers=headers).status_code == 200
