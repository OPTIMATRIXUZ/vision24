import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.security import ACCESS_COOKIE, REFRESH_COOKIE

pytestmark = [pytest.mark.db]

NEW_PASSWORD = "another-perfectly-fine-passphrase"


def bearer(res) -> dict:
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def as_key(client, secret: str) -> dict:
    client.cookies.clear()
    return {"Authorization": f"Bearer {secret}"}


class TestRegistration:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "allow_public_registration", True)

    def test_creates_a_tenant_a_site_and_an_owner(self, client, db):
        res = client.post(
            "/api/auth/register",
            json={
                "email": "founder@example.com",
                "password": NEW_PASSWORD,
                "company_name": "Coffee House",
            },
        )

        assert res.status_code == 200
        assert res.json()["user"]["role"] == "owner"
        site = client.get("/api/site", headers=bearer(res))
        assert site.status_code == 200

    def test_is_off_by_default(self, client, monkeypatch):
        monkeypatch.setattr(settings, "allow_public_registration", False)

        res = client.post(
            "/api/auth/register",
            json={"email": "x@example.com", "password": NEW_PASSWORD, "company_name": "X"},
        )

        assert res.status_code == 503

    def test_a_duplicate_email_is_rejected(self, client, tenant, make_user):
        existing = make_user(tenant)

        res = client.post(
            "/api/auth/register",
            json={"email": existing.email, "password": NEW_PASSWORD, "company_name": "X"},
        )

        assert res.status_code == 409

    def test_the_email_is_normalised(self, client, db):
        client.post(
            "/api/auth/register",
            json={
                "email": "  MiXeD@Example.COM  ",
                "password": NEW_PASSWORD,
                "company_name": "X",
            },
        )

        res = client.post(
            "/api/auth/login", json={"email": "mixed@example.com", "password": NEW_PASSWORD}
        )
        assert res.status_code == 200

    def test_a_short_password_is_rejected(self, client):
        res = client.post(
            "/api/auth/register",
            json={"email": "x@example.com", "password": "short", "company_name": "X"},
        )

        assert res.status_code == 422

    def test_two_companies_with_the_same_name_get_distinct_slugs(self, client, db):
        from app.models import Tenant

        for email in ("a@example.com", "b@example.com"):
            res = client.post(
                "/api/auth/register",
                json={"email": email, "password": NEW_PASSWORD, "company_name": "Coffee House"},
            )
            assert res.status_code == 200

        slugs = [t.slug for t in db.scalars(_select_tenants(Tenant)) if "coffee" in t.slug]
        assert len(slugs) == len(set(slugs)) == 2


class TestLogin:
    def test_works_with_no_authorization_header_at_all(self, client, owner, site, password):
        res = client.post("/api/auth/login", json={"email": owner.email, "password": password})

        assert res.status_code == 200
        assert res.json()["user"]["email"] == owner.email

    def test_sets_both_cookies(self, client, owner, sign_in):
        sign_in(owner)

        assert ACCESS_COOKIE in client.cookies
        assert REFRESH_COOKIE in client.cookies

    def test_the_returned_token_authenticates(self, client, owner, site, sign_in):
        res = sign_in(owner)

        assert client.get("/api/site", headers=bearer(res)).status_code == 200

    def test_a_wrong_password_is_rejected(self, client, owner, sign_in):
        assert sign_in(owner, "not-the-password").status_code == 401

    def test_an_unknown_address_and_a_wrong_password_are_indistinguishable(
        self, client, owner, sign_in, password
    ):
        wrong_password = sign_in(owner, "not-the-password")
        unknown = client.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": password}
        )

        assert wrong_password.status_code == unknown.status_code == 401
        assert wrong_password.json()["error"]["message"] == unknown.json()["error"]["message"]

    def test_a_deactivated_user_cannot_sign_in(self, client, tenant, make_user, sign_in):
        disabled = make_user(tenant, is_active=False)

        res = sign_in(disabled)

        assert res.status_code == 401

    def test_last_login_is_recorded(self, client, db, owner, sign_in):
        assert owner.last_login_at is None

        sign_in(owner)

        db.refresh(owner)
        assert owner.last_login_at is not None


class TestRefresh:
    def test_rotates_to_a_new_pair(self, client, owner, site, sign_in):
        first = sign_in(owner).json()["access_token"]

        res = client.post("/api/auth/refresh")

        assert res.status_code == 200
        assert res.json()["access_token"] != first
        assert client.get("/api/site", headers=bearer(res)).status_code == 200

    def test_a_replayed_refresh_token_revokes_the_whole_chain(
        self, client, db, owner, site, sign_in
    ):
        sign_in(owner)
        stolen = client.cookies[REFRESH_COOKIE]

        rotated = client.post("/api/auth/refresh")
        assert rotated.status_code == 200

        client.cookies.set(REFRESH_COOKIE, stolen)
        replay = client.post("/api/auth/refresh")
        assert replay.status_code == 401

        from app.models import UserSession

        live = [
            s for s in db.scalars(_select_sessions(UserSession, owner.id)) if s.revoked_at is None
        ]
        assert live == [], "a session survived a detected token replay"

    def test_a_used_token_cannot_be_reused_even_without_a_second_rotation(
        self, client, owner, site, sign_in
    ):
        sign_in(owner)
        first_refresh = client.cookies[REFRESH_COOKIE]
        client.post("/api/auth/refresh")

        client.cookies.set(REFRESH_COOKIE, first_refresh)
        assert client.post("/api/auth/refresh").status_code == 401

    def test_an_unknown_token_is_rejected(self, client, owner, sign_in):
        sign_in(owner)
        client.cookies.set(REFRESH_COOKIE, "not-a-real-token")

        assert client.post("/api/auth/refresh").status_code == 401

    def test_no_cookie_at_all_is_rejected(self, client):
        assert client.post("/api/auth/refresh").status_code == 401

    def test_an_expired_refresh_token_is_rejected(self, client, db, owner, sign_in):
        from app.models import UserSession

        sign_in(owner)
        session = db.scalars(_select_sessions(UserSession, owner.id)).first()
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.flush()

        assert client.post("/api/auth/refresh").status_code == 401

    def test_a_deactivated_user_cannot_refresh(self, client, db, owner, sign_in):
        sign_in(owner)
        owner.is_active = False
        db.flush()

        assert client.post("/api/auth/refresh").status_code == 401


class TestLogout:
    def test_revokes_the_session_and_clears_the_cookies(self, client, db, owner, site, sign_in):
        res = sign_in(owner)

        out = client.post("/api/auth/logout", headers=bearer(res))

        assert out.status_code == 200
        assert not client.cookies.get(ACCESS_COOKIE)
        assert client.post("/api/auth/refresh").status_code == 401

    def test_is_idempotent(self, client, owner, site, sign_in):
        res = sign_in(owner)
        headers = bearer(res)

        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 200


def test_whoami_describes_each_credential_kind(
    client, owner, site, tenant, sign_in, make_api_key, admin_headers
):
    signed_in = client.get("/api/auth/me", headers=bearer(sign_in(owner))).json()
    assert signed_in["kind"] == "user"
    assert signed_in["user"]["email"] == owner.email

    full, _key = make_api_key(tenant)
    by_key = client.get("/api/auth/me", headers=as_key(client, full)).json()
    assert by_key["kind"] == "api_key"
    assert by_key["user"] is None

    client.cookies.clear()
    by_token = client.get("/api/auth/me", headers=admin_headers).json()
    assert by_token["kind"] == "user"


class TestUserManagement:
    def test_an_owner_can_list_and_create(self, client, tenant, owner, sign_in):
        headers = bearer(sign_in(owner))

        created = client.post(
            "/api/users",
            headers=headers,
            json={"email": "staff@example.com", "password": NEW_PASSWORD, "role": "viewer"},
        )
        listed = client.get("/api/users", headers=headers)

        assert created.status_code == 200
        assert listed.status_code == 200
        assert {u["email"] for u in listed.json()} == {owner.email, "staff@example.com"}

    def test_a_viewer_cannot_list_users(self, client, tenant, make_user, sign_in):
        viewer = make_user(tenant, role="viewer")

        res = client.get("/api/users", headers=bearer(sign_in(viewer)))

        assert res.status_code == 403

    def test_an_admin_cannot_create_an_owner(self, client, tenant, make_user, sign_in):
        admin = make_user(tenant, role="admin")

        res = client.post(
            "/api/users",
            headers=bearer(sign_in(admin)),
            json={"email": "new@example.com", "password": NEW_PASSWORD, "role": "owner"},
        )

        assert res.status_code == 403

    def test_an_admin_can_create_a_viewer(self, client, tenant, make_user, sign_in):
        admin = make_user(tenant, role="admin")

        res = client.post(
            "/api/users",
            headers=bearer(sign_in(admin)),
            json={"email": "new@example.com", "password": NEW_PASSWORD, "role": "viewer"},
        )

        assert res.status_code == 200

    def test_an_admin_cannot_change_a_role(self, client, tenant, make_user, sign_in):
        admin = make_user(tenant, role="admin")
        victim = make_user(tenant, role="viewer")

        res = client.patch(
            f"/api/users/{victim.id}", headers=bearer(sign_in(admin)), json={"role": "owner"}
        )

        assert res.status_code == 403

    def test_the_last_owner_cannot_be_demoted(self, client, tenant, owner, sign_in):
        res = client.patch(
            f"/api/users/{owner.id}", headers=bearer(sign_in(owner)), json={"role": "viewer"}
        )

        assert res.status_code == 409

    def test_an_owner_can_be_demoted_when_another_exists(
        self, client, tenant, owner, make_user, sign_in
    ):
        second = make_user(tenant, role="owner")

        res = client.patch(
            f"/api/users/{second.id}", headers=bearer(sign_in(owner)), json={"role": "viewer"}
        )

        assert res.status_code == 200
        assert res.json()["role"] == "viewer"

    def test_you_cannot_deactivate_yourself(self, client, tenant, owner, make_user, sign_in):
        make_user(tenant, role="owner")

        res = client.delete(f"/api/users/{owner.id}", headers=bearer(sign_in(owner)))

        assert res.status_code == 403

    def test_changing_a_role_kills_the_users_live_sessions(
        self, client, tenant, owner, make_user, sign_in, site
    ):
        victim = make_user(tenant, role="admin")
        victim_headers = bearer(sign_in(victim))
        victim_refresh = client.cookies[REFRESH_COOKIE]
        assert client.get("/api/site", headers=victim_headers).status_code == 200

        client.cookies.clear()
        patched = client.patch(
            f"/api/users/{victim.id}", headers=bearer(sign_in(owner)), json={"role": "viewer"}
        )
        assert patched.status_code == 200

        client.cookies.clear()
        client.cookies.set(REFRESH_COOKIE, victim_refresh)
        assert client.post("/api/auth/refresh").status_code == 401

    def test_a_user_of_another_tenant_is_not_visible(
        self, client, tenant, owner, second_tenant, make_user, sign_in
    ):
        make_user(second_tenant, role="owner", email="theirs@example.com")

        listed = client.get("/api/users", headers=bearer(sign_in(owner))).json()

        assert "theirs@example.com" not in {u["email"] for u in listed}

    def test_another_tenants_user_cannot_be_patched(
        self, client, tenant, owner, second_tenant, make_user, sign_in
    ):
        theirs = make_user(second_tenant, role="viewer")

        res = client.patch(
            f"/api/users/{theirs.id}", headers=bearer(sign_in(owner)), json={"role": "owner"}
        )

        assert res.status_code == 404


class TestApiKeyManagement:
    def test_create_returns_a_working_secret_once(self, client, tenant, owner, site, sign_in):
        headers = bearer(sign_in(owner))

        created = client.post("/api/api-keys", headers=headers, json={"name": "CI"})

        assert created.status_code == 200
        secret = created.json()["secret"]
        assert client.get("/api/site", headers=as_key(client, secret)).status_code == 200

        listed = client.get("/api/api-keys", headers=headers).json()
        assert "secret" not in listed[0]
        assert secret not in str(listed)

    def test_revoking_stops_the_key_working(self, client, tenant, owner, site, sign_in):
        headers = bearer(sign_in(owner))
        created = client.post("/api/api-keys", headers=headers, json={"name": "CI"}).json()
        key_headers = as_key(client, created["secret"])
        assert client.get("/api/site", headers=key_headers).status_code == 200

        client.delete(f"/api/api-keys/{created['key']['id']}", headers=headers)

        assert client.get("/api/site", headers=key_headers).status_code == 401

    def test_an_admin_cannot_issue_keys(self, client, tenant, make_user, sign_in):
        admin = make_user(tenant, role="admin")

        res = client.post("/api/api-keys", headers=bearer(sign_in(admin)), json={"name": "CI"})

        assert res.status_code == 403

    def test_an_api_key_cannot_issue_another_api_key(
        self, client, tenant, owner, site, make_api_key
    ):
        full, _key = make_api_key(tenant, role="owner")

        res = client.post(
            "/api/api-keys", headers=as_key(client, full), json={"name": "persistence"}
        )

        assert res.status_code == 403

    def test_a_key_can_be_pinned_to_a_site(
        self, client, tenant, owner, site, tenant_second_site, sign_in
    ):
        headers = bearer(sign_in(owner))

        created = client.post(
            "/api/api-keys",
            headers=headers,
            json={"name": "store B", "site_id": str(tenant_second_site.id)},
        ).json()

        res = client.get("/api/site", headers=as_key(client, created["secret"]))
        assert res.json()["id"] == str(tenant_second_site.id)

    def test_a_key_cannot_be_pinned_to_another_tenants_site(
        self, client, tenant, owner, site, other_site, sign_in
    ):
        res = client.post(
            "/api/api-keys",
            headers=bearer(sign_in(owner)),
            json={"name": "theirs", "site_id": str(other_site.id)},
        )

        assert res.status_code == 404

    def test_another_tenants_key_cannot_be_revoked(
        self, client, tenant, owner, second_tenant, make_api_key, sign_in
    ):
        _full, theirs = make_api_key(second_tenant)

        res = client.delete(f"/api/api-keys/{theirs.id}", headers=bearer(sign_in(owner)))

        assert res.status_code == 404


@pytest.mark.unit
def test_the_cli_takes_no_password_on_the_command_line():
    from scripts.create_user import build_parser

    flags = {option for action in build_parser()._actions for option in action.option_strings}

    assert "--password" not in flags
    assert "--email" in flags


def _select_tenants(Tenant):
    from sqlalchemy import select

    return select(Tenant)


def _select_sessions(UserSession, user_id: uuid.UUID):
    from sqlalchemy import select

    return select(UserSession).where(UserSession.user_id == user_id)
