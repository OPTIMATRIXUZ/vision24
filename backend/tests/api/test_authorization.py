import pytest

from app.config import settings

pytestmark = [pytest.mark.db]

UUID0 = "00000000-0000-0000-0000-000000000000"

ZONE_BODY = {
    "camera_id": UUID0,
    "name": "z",
    "kind": "entrance",
    "polygon": [[0, 0], [1, 0], [1, 1]],
}

ADMIN_ROUTES = [
    ("POST", "/api/zones", ZONE_BODY),
    ("PUT", f"/api/zones/{UUID0}", ZONE_BODY),
    ("DELETE", f"/api/zones/{UUID0}", None),
    ("POST", "/api/alert-rules", {"zone_id": UUID0, "metric": "queue_len", "threshold": 3}),
    ("PUT", f"/api/alert-rules/{UUID0}", {"zone_id": UUID0, "metric": "queue_len", "threshold": 3}),
    ("DELETE", f"/api/alert-rules/{UUID0}", None),
    ("PUT", "/api/site", {"timezone": "Asia/Tashkent", "closing_time": "21:00:00"}),
    ("POST", "/api/sources/cctv", {"rtsp_url": "rtsp://x/y", "name": "c"}),
    ("POST", f"/api/sources/{UUID0}/capture", {"duration_s": 30}),
    ("POST", f"/api/sources/{UUID0}/capture/stop", None),
    ("POST", "/api/sources/analyze-all", None),
    ("POST", "/api/sources/demo", None),
    ("DELETE", f"/api/sources/{UUID0}", None),
    ("POST", f"/api/videos/{UUID0}/analyze", {}),
    ("POST", "/api/live/replay", None),
]

READ_ONLY_POSTS = [
    ("POST", "/api/chat", {"session_id": "s", "message": "hi"}),
    ("POST", "/api/tts", {"text": "hello"}),
    ("POST", "/api/sources/cctv/test", {"rtsp_url": "rtsp://x/y"}),
]


def call(client, method, path, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs)


@pytest.fixture
def viewer_headers(client, tenant, make_user, sign_in):
    viewer = make_user(tenant, role="viewer")
    headers = {"Authorization": f"Bearer {sign_in(viewer).json()['access_token']}"}
    client.cookies.clear()
    return headers


@pytest.fixture
def admin_headers(client, tenant, make_user, sign_in):
    admin = make_user(tenant, role="admin")
    headers = {"Authorization": f"Bearer {sign_in(admin).json()['access_token']}"}
    client.cookies.clear()
    return headers


class TestAdminCeiling:

    def test_an_admin_cannot_reset(self, client, admin_headers, tenant, site):
        res = client.post("/api/reset", headers=admin_headers, json={"confirm": tenant.slug})

        assert res.status_code == 403

    def test_an_admin_can_manage_users_but_not_mint_owners(self, client, admin_headers, site):
        listed = client.get("/api/users", headers=admin_headers)
        created = client.post(
            "/api/users",
            headers=admin_headers,
            json={"email": "x@example.com", "password": "a-long-enough-passphrase"},
        )

        assert listed.status_code == 200, "admin may list users"
        assert created.status_code == 200, "admin may create a viewer"

    def test_an_admin_cannot_issue_api_keys(self, client, admin_headers, site):
        res = client.post("/api/api-keys", headers=admin_headers, json={"name": "CI"})

        assert res.status_code == 403

    def test_an_admin_still_runs_the_dashboard(self, client, admin_headers, site, camera, db):
        res = client.get("/api/sources", headers=admin_headers)

        assert res.status_code == 200


class TestResetAuthorization:
    def test_an_owner_with_the_right_slug_succeeds(self, client, owner_headers, tenant, site):
        res = client.post("/api/reset", headers=owner_headers, json={"confirm": tenant.slug})

        assert res.status_code == 200

    def test_a_viewer_is_refused(self, client, viewer_headers, tenant, site):
        res = client.post("/api/reset", headers=viewer_headers, json={"confirm": tenant.slug})

        assert res.status_code == 403

    def test_an_admin_is_refused(self, client, admin_headers, tenant, site):
        res = client.post("/api/reset", headers=admin_headers, json={"confirm": tenant.slug})

        assert res.status_code == 403

    def test_a_wrong_confirmation_leaves_the_data_alone(
        self, client, owner_headers, tenant, site, camera, db
    ):
        from app.models import Camera

        res = client.post("/api/reset", headers=owner_headers, json={"confirm": "not-the-slug"})

        assert res.status_code == 400
        db.expire_all()
        assert db.get(Camera, camera.id) is not None, "data was destroyed despite the refusal"

    def test_the_error_says_what_to_type(self, client, owner_headers, tenant, site):
        res = client.post("/api/reset", headers=owner_headers, json={"confirm": "wrong"})

        assert tenant.slug in res.json()["error"]["message"]

    def test_a_missing_confirmation_is_rejected(self, client, owner_headers, tenant, site):
        res = client.post("/api/reset", headers=owner_headers, json={})

        assert res.status_code == 422

    def test_another_tenants_slug_does_not_work(
        self, client, owner_headers, tenant, site, second_tenant
    ):
        res = client.post("/api/reset", headers=owner_headers, json={"confirm": second_tenant.slug})

        assert res.status_code == 400

    def test_the_kill_switch_beats_a_correct_request(
        self, client, owner_headers, tenant, site, monkeypatch
    ):
        monkeypatch.setattr(settings, "allow_reset", False)

        res = client.post("/api/reset", headers=owner_headers, json={"confirm": tenant.slug})

        assert res.status_code == 503


class TestViewer:
    def test_a_viewer_can_read(self, client, viewer_headers, site, camera):
        for path in ("/api/site", "/api/cameras", "/api/zones", "/api/sources", "/api/alerts"):
            assert client.get(path, headers=viewer_headers).status_code == 200, path

    @pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES, ids=lambda v: str(v)[:40])
    def test_a_viewer_cannot_write(self, client, viewer_headers, site, method, path, body):
        res = call(client, method, path, body, viewer_headers)

        assert res.status_code == 403, (
            f"{method} {path} returned {res.status_code} for a viewer. If this route "
            f"does not change data, take it out of ADMIN_ROUTES and its decorator."
        )

    @pytest.mark.parametrize(("method", "path", "body"), READ_ONLY_POSTS, ids=lambda v: str(v)[:40])
    def test_a_viewer_keeps_the_posts_that_only_read(
        self, client, viewer_headers, site, method, path, body
    ):
        res = call(client, method, path, body, viewer_headers)

        assert res.status_code != 403, f"{method} {path} was gated as a write"

    def test_a_viewer_can_stream_a_chat_turn(self, client, viewer_headers, site):
        res = client.post(
            "/api/chat/stream",
            headers=viewer_headers,
            json={"session_id": "s", "message": "hi"},
        )

        assert res.status_code != 403


class TestAdmin:
    @pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES, ids=lambda v: str(v)[:40])
    def test_an_admin_is_never_refused_by_role(
        self, client, admin_headers, site, method, path, body
    ):
        res = call(client, method, path, body, admin_headers)

        assert res.status_code != 403, f"{method} {path} refused an admin"

    def test_an_admin_cannot_reach_the_three_owner_routes(
        self, client, admin_headers, tenant, site
    ):
        assert (
            client.post(
                "/api/reset", headers=admin_headers, json={"confirm": tenant.slug}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/api-keys", headers=admin_headers, json={"name": "x"}).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/users/{UUID0}", headers=admin_headers, json={"role": "owner"}
            ).status_code
            == 403
        )


def test_an_unknown_role_is_denied_rather_than_granted(
    client, db, tenant, site, make_user, sign_in
):
    stranger = make_user(tenant, role="superuser")

    headers = {"Authorization": f"Bearer {sign_in(stranger).json()['access_token']}"}
    client.cookies.clear()

    assert client.get("/api/site", headers=headers).status_code == 200
    assert client.post("/api/zones", headers=headers, json=ZONE_BODY).status_code == 403


def test_authorization_never_turns_into_authentication(client, viewer_headers, site):
    res = client.post("/api/zones", headers=viewer_headers, json=ZONE_BODY)

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"
