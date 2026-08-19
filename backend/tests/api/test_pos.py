from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytestmark = [pytest.mark.db]


def _today_at(site, hour, minute=0, second=0):
    tz = ZoneInfo(site.timezone)
    return datetime.now(tz).replace(hour=hour, minute=minute, second=second, microsecond=0)


@pytest.fixture
def checkout(site, camera, make_zone):
    return make_zone(site, camera, name="Checkout", kind="checkout_area")


@pytest.fixture
def presence_10_00(checkout, camera, make_event):

    def _seed(site, zone=None, start_hour=10, start_minute=0, start_second=0):
        zone = zone or checkout
        t0 = _today_at(site, start_hour, start_minute, start_second)
        for i in range(7):
            make_event(camera, zone, type="occupancy", ts=t0 + timedelta(seconds=4 * i), count=1)
        return t0

    return _seed


@pytest.fixture
def watching(checkout, camera, make_event):

    def _seed(ts, zone=None):
        make_event(camera, zone or checkout, type="occupancy", ts=ts, count=0)
        return ts

    return _seed


@pytest.fixture
def idle_coverage(checkout, camera, make_event):

    def _seed(site, start_minute, end_minute, zone=None, step_s=30):
        zone = zone or checkout
        ts, end = _today_at(site, 10, start_minute), _today_at(site, 10, end_minute)
        while ts <= end:
            make_event(camera, zone, type="occupancy", ts=ts, count=0)
            ts += timedelta(seconds=step_s)

    return _seed


def _receipt(external_id, ts, kind="sale", total=45_000, zone_id=None):
    body = {
        "external_id": external_id,
        "kind": kind,
        "ts": ts.isoformat(),
        "total": total,
        "items": [{"sku": "4780001", "name": "Coca-Cola 1.5L", "qty": 3, "unit_price": 15_000}],
    }
    if zone_id is not None:
        body["zone_id"] = str(zone_id)
    return body


def _post_receipts(client, headers, *receipts):
    return client.post("/api/pos/receipts", headers=headers, json={"receipts": list(receipts)})


class TestIngest:
    def test_idempotent_on_external_id(self, client, admin_headers, site, checkout):
        ts = _today_at(site, 10)
        first = _post_receipts(client, admin_headers, _receipt("R-1", ts))
        assert first.status_code == 200
        assert first.json() == {"ingested": 1, "duplicates": 0}

        again = _post_receipts(client, admin_headers, _receipt("R-1", ts))
        assert again.json() == {"ingested": 0, "duplicates": 1}

        feed = client.get("/api/pos/receipts", headers=admin_headers).json()
        assert len(feed) == 1

    def test_viewer_cannot_ingest(self, client, tenant, site, make_access_token):
        headers = {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}
        res = _post_receipts(client, headers, _receipt("R-1", _today_at(site, 10)))
        assert res.status_code == 403

    def test_foreign_zone_id_is_rejected(self, client, admin_headers, site, other_zone):
        res = _post_receipts(
            client, admin_headers, _receipt("R-1", _today_at(site, 10), zone_id=other_zone.id)
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "validation_error"

    def test_total_defaults_to_item_arithmetic(self, client, admin_headers, site):
        body = _receipt("R-1", _today_at(site, 10))
        body["total"] = 0
        _post_receipts(client, admin_headers, body)
        feed = client.get("/api/pos/receipts", headers=admin_headers).json()
        assert feed[0]["total"] == 45_000


class TestReconcile:
    def test_sale_with_presence_is_clean_and_without_is_flagged(
        self, client, admin_headers, site, checkout, presence_10_00, watching
    ):
        t0 = presence_10_00(site)
        ghost_ts = watching(_today_at(site, 11))
        _post_receipts(
            client,
            admin_headers,
            _receipt("R-OK", t0 + timedelta(seconds=12)),
            _receipt("R-GHOST", ghost_ts),
        )
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        assert out["receipts_total"] == 2
        flags = {
            d["receipt"]["external_id"]: d["flag"] for d in out["discrepancies"] if d["receipt"]
        }
        assert flags == {"R-GHOST": "no_person_at_sale"}
        assert out["unverified_receipts"] == 0

    def test_sale_the_camera_never_saw_is_unverified_not_flagged(
        self, client, admin_headers, site, checkout, presence_10_00
    ):
        t0 = presence_10_00(site)
        _post_receipts(
            client,
            admin_headers,
            _receipt("R-OK", t0 + timedelta(seconds=12)),
            _receipt("R-BLIND", _today_at(site, 11)),
        )
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        assert out["receipts_total"] == 2
        assert out["discrepancies"] == []
        assert out["unverified_receipts"] == 1

    def test_void_without_customer_is_flagged(
        self, client, admin_headers, site, checkout, presence_10_00, watching
    ):
        t0 = presence_10_00(site)
        ghost_ts = watching(_today_at(site, 11, 30))
        _post_receipts(
            client,
            admin_headers,
            _receipt("V-OK", t0 + timedelta(seconds=8), kind="void"),
            _receipt("V-GHOST", ghost_ts, kind="void"),
        )
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        flags = {
            d["receipt"]["external_id"]: d["flag"] for d in out["discrepancies"] if d["receipt"]
        }
        assert flags == {"V-GHOST": "void_no_customer"}

    def test_visit_without_receipt_is_flagged_until_a_sale_appears(
        self, client, admin_headers, site, checkout, presence_10_00
    ):
        t0 = presence_10_00(site)
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        assert [d["flag"] for d in out["discrepancies"]] == ["unscanned_visit"]
        assert out["discrepancies"][0]["receipt"] is None

        _post_receipts(client, admin_headers, _receipt("R-LATE", t0 + timedelta(seconds=10)))
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        assert out["discrepancies"] == []

    def test_one_sale_cannot_vouch_for_two_back_to_back_visits(
        self, client, admin_headers, site, checkout, presence_10_00
    ):
        presence_10_00(site, start_minute=0)
        t1 = presence_10_00(site, start_second=50)

        _post_receipts(client, admin_headers, _receipt("R-ONE", t1 + timedelta(seconds=2)))
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        assert [d["flag"] for d in out["discrepancies"]] == ["unscanned_visit"]

    def test_receipt_pinned_to_a_register_ignores_other_registers(
        self, client, admin_headers, site, camera, checkout, make_zone, presence_10_00, watching
    ):
        second = make_zone(site, camera, name="Checkout 2", kind="checkout_area")
        t0 = presence_10_00(site)
        watching(t0 + timedelta(seconds=12), zone=second)
        _post_receipts(
            client,
            admin_headers,
            _receipt("R-PINNED", t0 + timedelta(seconds=12), zone_id=second.id),
        )
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        pinned = [d for d in out["discrepancies"] if d["receipt"]]
        assert [d["flag"] for d in pinned] == ["no_person_at_sale"]

    def test_no_checkout_zone_means_no_verdicts(self, client, admin_headers, site, camera, zone):
        _post_receipts(client, admin_headers, _receipt("R-1", _today_at(site, 10)))
        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        assert out["receipts_total"] == 1
        assert out["discrepancies"] == []

    def test_flag_carries_nearby_evidence_frame(
        self, client, admin_headers, site, camera, checkout, make_event, make_clip, watching
    ):
        ghost_ts = watching(_today_at(site, 11))
        witness = make_event(camera, checkout, ts=ghost_ts + timedelta(seconds=40))
        make_clip(witness)
        _post_receipts(client, admin_headers, _receipt("R-GHOST", ghost_ts))

        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        (flag,) = out["discrepancies"]
        assert flag["evidence_event_id"] == witness.id
        assert flag["snapshot_url"].startswith("https://fake/snapshots/")

    def test_receipt_feed_is_annotated_with_flags(
        self, client, admin_headers, site, checkout, presence_10_00, watching
    ):
        t0 = presence_10_00(site)
        ghost_ts = watching(_today_at(site, 11))
        _post_receipts(
            client,
            admin_headers,
            _receipt("R-OK", t0 + timedelta(seconds=12)),
            _receipt("R-GHOST", ghost_ts),
        )
        feed = {
            r["external_id"]: r
            for r in client.get("/api/pos/receipts", headers=admin_headers).json()
        }
        assert feed["R-OK"]["flag"] is None
        assert feed["R-GHOST"]["flag"] == "no_person_at_sale"

    def test_other_tenants_receipts_are_absent(
        self, client, admin_headers, db, site, other_site, checkout
    ):
        from app.models import PosReceipt

        db.add(
            PosReceipt(
                site_id=other_site.id,
                external_id="THEIRS-1",
                kind="sale",
                ts=_today_at(other_site, 10),
                total=1,
            )
        )
        db.flush()
        assert client.get("/api/pos/receipts", headers=admin_headers).json() == []


class TestViewerAccess:

    def _viewer(self, tenant, make_access_token):
        return {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}

    def test_viewer_cannot_read_pos_data(self, client, tenant, site, make_access_token):
        headers = self._viewer(tenant, make_access_token)
        for path in ("/api/pos/receipts", "/api/pos/discrepancies", "/api/pos/visits"):
            assert client.get(path, headers=headers).status_code == 403, path

    def test_viewer_still_reads_savings(self, client, tenant, site, make_access_token):
        headers = self._viewer(tenant, make_access_token)
        assert client.get("/api/savings", headers=headers).status_code == 200

    def test_admin_reads_pos_data(self, client, admin_headers, site):
        for path in ("/api/pos/receipts", "/api/pos/discrepancies", "/api/pos/visits"):
            assert client.get(path, headers=admin_headers).status_code == 200, path


class TestVisitVerdicts:

    def _verdict(self, make_event, camera, checkout, site, *, kind, confidence, items=None):
        return make_event(
            camera,
            checkout,
            type="checkout_visit",
            ts=_today_at(site, 10, 0, 0),
            ts_end=_today_at(site, 10, 0, 24),
            kind=kind,
            confidence=confidence,
            items=items or [],
            notes="supplier delivering stock",
        )

    def test_confident_administrative_visit_is_cleared(
        self, client, admin_headers, site, camera, checkout, presence_10_00, make_event
    ):
        presence_10_00(site)
        self._verdict(make_event, camera, checkout, site, kind="administrative", confidence=0.95)

        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        (flag,) = out["discrepancies"]
        assert flag["flag"] == "unscanned_visit"
        assert flag["status"] == "cleared"
        assert "AI review" in flag["explanation"]

        lines = {
            line["key"]: line
            for line in client.get("/api/savings", headers=admin_headers).json()["lines"]
        }
        assert lines["pos"]["count"] == 0

    def test_low_confidence_administrative_stays_open(
        self, client, admin_headers, site, camera, checkout, presence_10_00, make_event
    ):
        presence_10_00(site)
        self._verdict(make_event, camera, checkout, site, kind="administrative", confidence=0.5)
        (flag,) = client.get("/api/pos/discrepancies", headers=admin_headers).json()[
            "discrepancies"
        ]
        assert flag["status"] == "open"

    def test_sale_verdict_keeps_flag_open_and_names_the_goods(
        self, client, admin_headers, site, camera, checkout, presence_10_00, make_event
    ):
        presence_10_00(site)
        verdict = self._verdict(
            make_event,
            camera,
            checkout,
            site,
            kind="sale",
            confidence=0.9,
            items=[{"name": "snack packet", "qty": 1}],
        )
        (flag,) = client.get("/api/pos/discrepancies", headers=admin_headers).json()[
            "discrepancies"
        ]
        assert flag["status"] == "open"
        assert flag["seen_items"] == [{"name": "snack packet", "qty": 1}]
        assert "snack packet ×1" in flag["explanation"]
        assert flag["evidence_event_id"] == verdict.id

    def test_visits_endpoint_joins_verdicts_and_receipts(
        self, client, admin_headers, site, camera, checkout, presence_10_00, make_event
    ):
        t0 = presence_10_00(site)
        self._verdict(
            make_event,
            camera,
            checkout,
            site,
            kind="sale",
            confidence=0.9,
            items=[{"name": "cola", "qty": 2}],
        )
        _post_receipts(client, admin_headers, _receipt("R-OK", t0 + timedelta(seconds=12)))

        (visit,) = client.get("/api/pos/visits", headers=admin_headers).json()
        assert visit["kind"] == "sale"
        assert visit["items"] == [{"name": "cola", "qty": 2}]
        assert visit["receipt"]["external_id"] == "R-OK"

    def test_visits_endpoint_lists_presence_even_without_verdicts(
        self, client, admin_headers, site, checkout, presence_10_00
    ):
        presence_10_00(site)
        (visit,) = client.get("/api/pos/visits", headers=admin_headers).json()
        assert visit["kind"] is None
        assert visit["items"] == []
        assert visit["receipt"] is None


class TestSimulate:
    def test_needs_a_checkout_zone(self, client, admin_headers, site, camera, zone):
        res = client.post("/api/pos/simulate", headers=admin_headers, json={})
        assert res.status_code == 400

    def test_viewer_cannot_simulate(self, client, tenant, site, make_access_token):
        headers = {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}
        assert client.post("/api/pos/simulate", headers=headers, json={}).status_code == 403

    def test_plants_every_flag_when_footage_allows(
        self, client, admin_headers, site, checkout, presence_10_00, idle_coverage
    ):
        presence_10_00(site, start_minute=0)
        presence_10_00(site, start_minute=10)
        idle_coverage(site, 2, 8)

        res = client.post("/api/pos/simulate", headers=admin_headers, json={})
        assert res.status_code == 200
        planted = res.json()["planted"]
        assert planted == {"no_person_at_sale": 1, "void_no_customer": 1, "unscanned_visit": 1}

        out = client.get("/api/pos/discrepancies", headers=admin_headers).json()
        flags = sorted(d["flag"] for d in out["discrepancies"])
        assert flags == ["no_person_at_sale", "unscanned_visit", "void_no_customer"]

    def test_deterministic_and_rerunnable(
        self, client, admin_headers, site, checkout, presence_10_00
    ):
        presence_10_00(site, start_minute=0)
        presence_10_00(site, start_minute=10)

        client.post("/api/pos/simulate", headers=admin_headers, json={})
        first = {
            (r["external_id"], r["ts"], r["kind"])
            for r in client.get("/api/pos/receipts", headers=admin_headers).json()
        }
        client.post("/api/pos/simulate", headers=admin_headers, json={})
        second = {
            (r["external_id"], r["ts"], r["kind"])
            for r in client.get("/api/pos/receipts", headers=admin_headers).json()
        }
        assert first == second
        assert len(first) == len({r[0] for r in first})

    def test_simulate_preserves_real_api_receipts(
        self, client, admin_headers, site, checkout, presence_10_00
    ):
        t0 = presence_10_00(site)
        _post_receipts(client, admin_headers, _receipt("REAL-1", t0 + timedelta(seconds=12)))
        client.post("/api/pos/simulate", headers=admin_headers, json={})
        ids = {
            r["external_id"] for r in client.get("/api/pos/receipts", headers=admin_headers).json()
        }
        assert "REAL-1" in ids


class TestSavings:
    def test_lines_are_count_times_constant(
        self,
        client,
        admin_headers,
        site,
        camera,
        checkout,
        make_event,
        make_alert_rule,
        watching,
    ):
        from app.config import settings

        make_alert_rule(checkout, metric="queue_len", threshold=3)
        base = _today_at(site, 12)
        for offset, qlen in ((0, 5), (2, 5), (4, 1), (6, 4)):
            make_event(
                camera,
                checkout,
                type="queue_len",
                ts=base + timedelta(seconds=offset),
                queue_len=qlen,
            )
        make_event(camera, checkout, type="entry", ts=_today_at(site, 22))
        make_event(camera, checkout, type="entry", ts=_today_at(site, 23))
        make_event(camera, checkout, type="delivery_trip", items=[], unmatched=2)
        ghost_ts = watching(_today_at(site, 11))
        _post_receipts(client, admin_headers, _receipt("R-GHOST", ghost_ts, total=45_000))

        out = client.get("/api/savings", headers=admin_headers).json()
        lines = {line["key"]: line for line in out["lines"]}
        assert lines["queues"]["count"] == 2
        assert lines["queues"]["amount"] == 2 * settings.savings_avg_check
        assert lines["after_hours"]["count"] == 2
        assert lines["after_hours"]["amount"] == 2 * settings.savings_after_hours_value
        assert lines["deliveries"]["count"] == 2
        assert lines["deliveries"]["amount"] == 2 * settings.savings_package_value
        assert lines["pos"]["count"] == 1
        assert lines["pos"]["amount"] == 45_000

        expected_total = (
            2 * settings.savings_avg_check
            + 2 * settings.savings_after_hours_value
            + 2 * settings.savings_package_value
            + 45_000
        )
        assert out["total"] == expected_total
        assert out["subscription"] == settings.subscription_price_month
        assert out["net"] == expected_total - settings.subscription_price_month
        assert out["constants"]["avg_check"] == settings.savings_avg_check

    def test_empty_month_is_all_zeros(self, client, auth_headers, site, checkout):
        out = client.get("/api/savings?month=2020-01", headers=auth_headers).json()
        assert out["month"] == "2020-01"
        assert all(line["count"] == 0 and line["amount"] == 0 for line in out["lines"])
        assert out["total"] == 0

    def test_malformed_month_is_400(self, client, auth_headers, site):
        assert client.get("/api/savings?month=nope", headers=auth_headers).status_code == 400


class TestReset:
    def test_reset_wipes_receipts(self, client, owner_headers, admin_headers, site, checkout):
        _post_receipts(client, admin_headers, _receipt("R-1", _today_at(site, 10)))
        res = client.post("/api/reset", headers=owner_headers, json={"confirm": "demo"})
        assert res.status_code == 200
        assert client.get("/api/pos/receipts", headers=admin_headers).json() == []


class TestChatTools:
    def _flag_a_ghost(self, client, admin_headers, site, watching):
        ghost_ts = watching(_today_at(site, 11))
        _post_receipts(client, admin_headers, _receipt("R-GHOST", ghost_ts))
        return ghost_ts

    def test_get_pos_discrepancies_tool(
        self, client, admin_headers, db, site, camera, checkout, make_event, make_clip, watching
    ):
        from app.services.ai.tools import ToolContext, dispatch

        ghost_ts = _today_at(site, 11)
        witness = make_event(camera, checkout, ts=ghost_ts + timedelta(seconds=30))
        make_clip(witness)
        self._flag_a_ghost(client, admin_headers, site, watching)

        ctx = ToolContext(db=db, site=site, zones=[checkout], tz=ZoneInfo(site.timezone))
        out = dispatch("get_pos_discrepancies", {}, ctx)

        assert out["receipts_total"] == 1
        (flag,) = out["discrepancies"]
        assert flag["flag"] == "no_person_at_sale"
        assert flag["receipt"]["external_id"] == "R-GHOST"
        assert flag["receipt"]["source"] == "api"
        assert witness.id in ctx.events_out
        assert ctx.events_out[witness.id]["snapshot_url"].startswith("https://fake/")

    def test_get_savings_tool(self, client, admin_headers, db, site, checkout, watching):
        from app.config import settings
        from app.services.ai.tools import ToolContext, dispatch

        self._flag_a_ghost(client, admin_headers, site, watching)
        ctx = ToolContext(db=db, site=site, zones=[checkout], tz=ZoneInfo(site.timezone))
        out = dispatch("get_savings", {}, ctx)

        lines = {line["key"]: line for line in out["lines"]}
        assert lines["pos"]["count"] == 1
        assert out["net"] == out["total"] - settings.subscription_price_month
        assert out["constants"]["avg_check"] == settings.savings_avg_check


class TestReportContext:
    def test_report_context_carries_pos_and_savings(
        self, client, admin_headers, db, site, checkout, watching
    ):
        from app.services import analytics

        _post_receipts(client, admin_headers, _receipt("R-GHOST", watching(_today_at(site, 11))))
        context = analytics.build_report_context(db, site)
        assert context["pos"]["receipts"] == 1
        assert context["pos"]["discrepancies"][0]["flag"] == "no_person_at_sale"
        assert context["savings"]["lines"] == [{"key": "pos", "count": 1, "amount": 45_000}]
        assert (
            context["savings"]["net"]
            == context["savings"]["total"] - context["savings"]["subscription"]
        )

    def test_fallback_report_renders_pos_and_savings_sections(
        self, client, admin_headers, db, site, checkout, watching
    ):
        from app.services.ai import report as report_service

        _post_receipts(client, admin_headers, _receipt("R-GHOST", watching(_today_at(site, 11))))
        out = report_service.generate_report(db, site, day=None)
        assert out.generated_by == "fallback"
        assert "## Сверка с кассой" in out.markdown
        assert "продажа при пустой кассе" in out.markdown
        assert "## Сэкономлено" in out.markdown
        assert "45 000 сум" in out.markdown
