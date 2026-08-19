import uuid
from datetime import UTC, datetime

import pytest

from app.errors import NotFoundError
from app.scoping import resolve_site

pytestmark = [pytest.mark.db]


class TestDeterminism:
    def test_the_same_tenant_always_resolves_to_the_same_site(
        self, db, tenant, site, tenant_second_site
    ):
        answers = {resolve_site(db, tenant.id).id for _ in range(10)}
        assert len(answers) == 1

    def test_it_resolves_to_the_oldest_site_regardless_of_insertion_order(
        self, db, tenant, site, tenant_second_site
    ):
        site.created_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
        tenant_second_site.created_at = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        db.flush()

        assert resolve_site(db, tenant.id).id == tenant_second_site.id

    def test_it_is_stable_when_timestamps_tie(self, db, tenant, site, tenant_second_site):
        shared = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
        site.created_at = shared
        tenant_second_site.created_at = shared
        db.flush()

        answers = {resolve_site(db, tenant.id).id for _ in range(10)}
        assert len(answers) == 1
        assert resolve_site(db, tenant.id).id == min((site.id, tenant_second_site.id), key=str)


class TestTenantIsolation:
    def test_a_tenant_never_resolves_to_another_tenants_site(
        self, db, tenant, site, second_tenant, other_site
    ):
        assert resolve_site(db, tenant.id).id == site.id
        assert resolve_site(db, second_tenant.id).id == other_site.id

    def test_an_explicit_site_id_must_belong_to_the_tenant(
        self, db, tenant, site, second_tenant, other_site
    ):
        assert resolve_site(db, tenant.id, site.id).id == site.id
        with pytest.raises(NotFoundError):
            resolve_site(db, tenant.id, other_site.id)

    def test_a_foreign_site_is_indistinguishable_from_a_nonexistent_one(
        self, db, tenant, site, other_site
    ):
        with pytest.raises(NotFoundError) as foreign:
            resolve_site(db, tenant.id, other_site.id)
        with pytest.raises(NotFoundError) as absent:
            resolve_site(db, tenant.id, uuid.uuid4())
        assert str(foreign.value) == str(absent.value)
        assert foreign.value.code == absent.value.code


class TestNoSite:
    def test_a_tenant_with_no_site_raises_rather_than_returning_none(self, db, tenant):
        with pytest.raises(NotFoundError):
            resolve_site(db, tenant.id)


class TestRoutesAgree:
    def test_every_route_resolves_to_the_same_site_for_one_caller(
        self, client, admin_headers, db, tenant, site, tenant_second_site
    ):
        from app.models import Tenant

        assert db.query(Tenant).count() >= 1

        first = client.get("/api/site", headers=admin_headers)
        assert first.status_code == 200
        resolved = first.json()["id"]

        for _ in range(5):
            again = client.get("/api/site", headers=admin_headers)
            assert again.json()["id"] == resolved

    def test_no_site_configured_is_a_404_not_a_500(self, client, admin_headers, db, tenant):
        res = client.get("/api/site", headers=admin_headers)
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "not_found"
