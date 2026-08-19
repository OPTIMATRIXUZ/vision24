import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import Settings, settings
from app.security import ACCESS_COOKIE, create_access_token

pytestmark = [pytest.mark.db]


def get_site(client, *, headers=None, cookie=None):
    if cookie is not None:
        client.cookies.set(ACCESS_COOKIE, cookie)
    return client.get("/api/site", headers=headers or {})


class TestRetiredStaticToken:

    def test_the_old_token_is_rejected(self, client, site):
        res = client.get("/api/site", headers={"Authorization": "Bearer dev-token"})

        assert res.status_code == 401
        assert res.json()["error"]["code"] == "unauthenticated"

    def test_an_unrecognised_bearer_is_rejected_rather_than_falling_through(self, client, site):
        for token in ["dev-token", "anything", "not.a.jwt.at.all", "v24k", ""]:
            res = client.get("/api/site", headers={"Authorization": f"Bearer {token}"})
            assert res.status_code == 401, f"{token!r} was accepted"

    def test_the_settings_are_gone(self):
        for name in ["api_token", "legacy_api_token_enabled", "legacy_tenant_slug"]:
            assert name not in Settings.model_fields, f"{name} is still a setting"

    def test_no_response_carries_the_deprecation_header(self, client, admin_headers, site):
        res = client.get("/api/site", headers=admin_headers)

        assert res.status_code == 200
        assert "X-Auth-Deprecated" not in res.headers


class TestAccessToken:
    def test_authenticates_from_the_header(self, client, site, auth_headers):
        res = client.get("/api/site", headers=auth_headers)

        assert res.status_code == 200
        assert res.json()["id"] == str(site.id)

    def test_authenticates_from_the_cookie(self, client, site, access_cookie):
        res = get_site(client, cookie=access_cookie)

        assert res.status_code == 200
        assert res.json()["id"] == str(site.id)

    def test_is_not_marked_deprecated(self, client, site, auth_headers):
        res = client.get("/api/site", headers=auth_headers)

        assert "X-Auth-Deprecated" not in res.headers

    def test_a_token_for_another_tenant_never_sees_this_ones_site(
        self, client, site, other_site, second_tenant, make_access_token
    ):
        token = make_access_token(second_tenant)

        res = client.get("/api/site", headers={"Authorization": f"Bearer {token}"})

        assert res.status_code == 200
        assert res.json()["id"] == str(other_site.id)
        assert res.json()["id"] != str(site.id)

    def test_an_expired_token_is_rejected(self, client, site, tenant, monkeypatch):
        monkeypatch.setattr(settings, "access_token_ttl_minutes", -5)
        token = create_access_token(
            user_id=uuid.uuid4(),
            tenant_id=tenant.id,
            session_id=uuid.uuid4(),
            role="owner",
        )

        res = client.get("/api/site", headers={"Authorization": f"Bearer {token}"})

        assert res.status_code == 401
        assert "session" in res.json()["error"]["message"].lower()

    def test_a_token_signed_with_another_key_is_rejected(self, client, site, tenant):
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "tid": str(tenant.id),
                "sid": str(uuid.uuid4()),
                "role": "owner",
                "typ": "access",
                "exp": 9_999_999_999,
            },
            "an-attackers-key-also-padded-past-the-32-byte-floor",
            algorithm="HS256",
        )

        res = client.get("/api/site", headers={"Authorization": f"Bearer {forged}"})

        assert res.status_code == 401

    def test_a_refresh_token_cannot_be_spent_as_an_access_token(self, client, site, tenant):
        refresh_shaped = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "tid": str(tenant.id),
                "sid": str(uuid.uuid4()),
                "typ": "refresh",
                "exp": 9_999_999_999,
            },
            settings.auth_secret_key,
            algorithm="HS256",
        )

        res = client.get("/api/site", headers={"Authorization": f"Bearer {refresh_shaped}"})

        assert res.status_code == 401

    def test_no_signing_key_is_401_not_500(self, client, site, auth_headers, monkeypatch):
        monkeypatch.setattr(settings, "auth_secret_key", "")

        res = client.get("/api/site", headers=auth_headers)

        assert res.status_code == 401
        assert res.json()["error"]["code"] == "unauthenticated"


class TestApiKey:
    def test_authenticates_and_resolves_its_tenant(
        self, client, site, other_site, second_tenant, make_api_key
    ):
        full, _ = make_api_key(second_tenant)

        res = client.get("/api/site", headers={"Authorization": f"Bearer {full}"})

        assert res.status_code == 200
        assert res.json()["id"] == str(other_site.id)

    def test_an_unknown_prefix_is_rejected(self, client, site):
        res = client.get("/api/site", headers={"Authorization": "Bearer v24k_deadbeef_nope"})

        assert res.status_code == 401

    def test_a_valid_prefix_with_the_wrong_secret_is_rejected(
        self, client, site, tenant, make_api_key
    ):
        full, _key = make_api_key(tenant)
        tampered = full.rsplit("_", 1)[0] + "_wrong-secret"

        res = client.get("/api/site", headers={"Authorization": f"Bearer {tampered}"})

        assert res.status_code == 401

    def test_a_revoked_key_is_rejected(self, client, db, site, tenant, make_api_key):
        full, _key = make_api_key(tenant, revoked_at=datetime.now(UTC))
        db.flush()

        res = client.get("/api/site", headers={"Authorization": f"Bearer {full}"})

        assert res.status_code == 401

    def test_an_expired_key_is_rejected(self, client, db, site, tenant, make_api_key):
        full, _key = make_api_key(tenant, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        db.flush()

        res = client.get("/api/site", headers={"Authorization": f"Bearer {full}"})

        assert res.status_code == 401

    def test_a_key_expiring_in_the_future_still_works(self, client, db, site, tenant, make_api_key):
        full, _key = make_api_key(tenant, expires_at=datetime.now(UTC) + timedelta(days=30))
        db.flush()

        res = client.get("/api/site", headers={"Authorization": f"Bearer {full}"})

        assert res.status_code == 200


class TestPrecedence:
    def test_a_valid_cookie_beats_a_header(
        self, client, site, other_site, second_tenant, make_access_token, admin_headers
    ):
        res = get_site(
            client,
            headers=admin_headers,
            cookie=make_access_token(second_tenant),
        )

        assert res.status_code == 200
        assert res.json()["id"] == str(other_site.id), "the header won"

    def test_a_stale_cookie_does_not_break_a_valid_header(self, client, site, admin_headers):
        res = get_site(client, headers=admin_headers, cookie="stale.garbage.value")

        assert res.status_code == 200
        assert res.json()["id"] == str(site.id)

    def test_a_stale_cookie_alone_falls_back_to_the_demo_owner(self, client, site):
        res = get_site(client, cookie="stale.garbage.value")

        assert res.status_code == 200
        assert res.json()["id"] == str(site.id)

    def test_no_credentials_resolves_the_demo_owner(self, client, site):
        res = client.get("/api/site")

        assert res.status_code == 200
        assert res.json()["id"] == str(site.id)

    def test_a_non_bearer_scheme_is_rejected(self, client, site):
        res = client.get("/api/site", headers={"Authorization": "Basic dXNlcjpwYXNz"})

        assert res.status_code == 401


def test_the_401_carries_a_challenge(client, site):
    res = client.get("/api/site", headers={"Authorization": "Bearer nope"})

    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"


class TestStartupGuard:

    def test_prod_without_a_signing_key_refuses_to_boot(self, monkeypatch):
        from app.deps import check_auth_config
        from app.errors import ConfigurationError

        monkeypatch.setattr(settings, "environment", "prod")
        monkeypatch.setattr(settings, "auth_secret_key", "")

        with pytest.raises(ConfigurationError, match="AUTH_SECRET_KEY"):
            check_auth_config()

    def test_dev_without_a_signing_key_still_boots(self, monkeypatch):
        from app.deps import check_auth_config

        monkeypatch.setattr(settings, "environment", "dev")
        monkeypatch.setattr(settings, "auth_secret_key", "")

        check_auth_config()

    def test_prod_with_a_signing_key_boots(self, monkeypatch):
        from app.deps import check_auth_config

        monkeypatch.setattr(settings, "environment", "prod")
        monkeypatch.setattr(settings, "auth_secret_key", "a-real-signing-key")

        check_auth_config()
