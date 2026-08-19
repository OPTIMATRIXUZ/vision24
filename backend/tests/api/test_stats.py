from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytestmark = [pytest.mark.db]


def _seed_day(site, camera, zone, make_event):
    tz = ZoneInfo(site.timezone)
    base = datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
    for i in range(3):
        make_event(camera, zone, type="entry", ts=base + timedelta(minutes=i))
    make_event(camera, None, type="occupancy", ts=base, count=4)
    make_event(camera, zone, type="dwell", ts=base, dwell_s=30)


class TestAggregate:
    def test_roundtrip_carries_the_days_numbers(
        self, client, admin_headers, site, camera, zone, make_event
    ):
        _seed_day(site, camera, zone, make_event)
        res = client.post("/api/stats/aggregate", headers=admin_headers, json={})
        assert res.status_code == 200
        out = res.json()
        assert out["visitors"] == 3
        assert out["peak_occupancy"] == 4
        assert out["peak_hour"] == 12
        assert out["avg_dwell_s"] == 30.0

        rows = client.get("/api/stats/daily", headers=admin_headers).json()
        assert len(rows) == 1
        assert rows[0]["visitors"] == 3

    def test_reaggregation_updates_rather_than_duplicates(
        self, client, admin_headers, site, camera, zone, make_event
    ):
        _seed_day(site, camera, zone, make_event)
        client.post("/api/stats/aggregate", headers=admin_headers, json={})
        make_event(camera, zone, type="entry")
        client.post("/api/stats/aggregate", headers=admin_headers, json={})

        rows = client.get("/api/stats/daily", headers=admin_headers).json()
        assert len(rows) == 1
        assert rows[0]["visitors"] == 4

    def test_no_pii_shape(self, client, admin_headers, site, camera, zone, make_event):
        _seed_day(site, camera, zone, make_event)
        client.post("/api/stats/aggregate", headers=admin_headers, json={})
        (row,) = client.get("/api/stats/daily", headers=admin_headers).json()
        assert set(row) == {
            "site_id",
            "day",
            "visitors",
            "peak_occupancy",
            "peak_hour",
            "avg_dwell_s",
            "queue_breaches",
            "alerts_count",
        }

    def test_other_tenants_rows_are_absent(self, client, admin_headers, db, site, other_site):
        from app.models import SiteDailyStats

        db.add(
            SiteDailyStats(
                site_id=other_site.id,
                day=datetime.now(ZoneInfo("UTC")).date(),
                visitors=99,
                peak_occupancy=9,
            )
        )
        db.flush()
        assert client.get("/api/stats/daily", headers=admin_headers).json() == []

    def test_viewer_cannot_aggregate(self, client, tenant, site, make_access_token):
        headers = {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}
        assert client.post("/api/stats/aggregate", headers=headers, json={}).status_code == 403


class TestOptIn:
    def test_owner_toggles_and_it_persists(self, client, owner_headers, db, tenant, site):
        assert client.get("/api/stats/opt-in", headers=owner_headers).json() == {"opt_in": False}
        res = client.put("/api/stats/opt-in", headers=owner_headers, json={"opt_in": True})
        assert res.json() == {"opt_in": True}
        db.refresh(tenant)
        assert tenant.stats_opt_in is True

    def test_admin_cannot_consent(self, client, admin_headers, site):
        res = client.put("/api/stats/opt-in", headers=admin_headers, json={"opt_in": True})
        assert res.status_code == 403
