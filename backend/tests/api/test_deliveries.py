from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytestmark = [pytest.mark.db]


@pytest.fixture
def product(db, site):
    from app.models import ProductType

    p = ProductType(site_id=site.id, name="Cola crate", units_per_package=24, unit_label="bottles")
    db.add(p)
    db.flush()
    return p


def _trip_attributes(product, count, unmatched=0):
    return {
        "direction": "in",
        "items": [
            {
                "product_type_id": str(product.id),
                "product_name": product.name,
                "count": count,
                "confidence": 0.8,
            }
        ],
        "unmatched": unmatched,
    }


class TestDeliverySummary:
    def test_totals_are_summed_and_units_derived(
        self, client, auth_headers, site, camera, zone, make_event, product
    ):
        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 2))
        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 3, unmatched=1))

        res = client.get("/api/deliveries", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["trips"]) == 2
        assert body["unmatched_packages"] == 1
        assert body["totals"] == [
            {
                "product_type_id": str(product.id),
                "product_name": "Cola crate",
                "packages": 5,
                "units": 120,
                "unit_label": "bottles",
            }
        ]

    def test_deleted_product_keeps_denormalized_name(
        self, client, auth_headers, site, camera, zone, make_event, product
    ):
        attrs = _trip_attributes(product, 2)
        attrs["items"][0]["product_type_id"] = None
        make_event(camera, zone, type="delivery_trip", **attrs)

        body = client.get("/api/deliveries", headers=auth_headers).json()
        assert body["totals"][0]["product_name"] == "Cola crate"
        assert body["totals"][0]["units"] is None

    def test_day_filter(self, client, auth_headers, site, camera, zone, make_event, product):
        tz = ZoneInfo(site.timezone)
        yesterday = datetime.now(tz) - timedelta(days=1)
        make_event(camera, zone, type="delivery_trip", ts=yesterday, **_trip_attributes(product, 4))
        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 1))

        today = client.get("/api/deliveries", headers=auth_headers).json()
        assert len(today["trips"]) == 1
        past = client.get(
            f"/api/deliveries?day={yesterday.date().isoformat()}", headers=auth_headers
        ).json()
        assert len(past["trips"]) == 1
        assert past["totals"][0]["packages"] == 4

    def test_camera_filter(
        self, client, auth_headers, site, camera, zone, make_camera, make_event, product
    ):
        second = make_camera(site, name="Second")
        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 2))
        make_event(second, None, type="delivery_trip", **_trip_attributes(product, 7))

        body = client.get(f"/api/deliveries?camera_id={camera.id}", headers=auth_headers).json()
        assert len(body["trips"]) == 1
        assert body["totals"][0]["packages"] == 2

    def test_camera_of_another_tenant_is_404(self, client, auth_headers, site, other_camera):
        res = client.get(f"/api/deliveries?camera_id={other_camera.id}", headers=auth_headers)
        assert res.status_code == 404

    def test_other_tenants_trips_are_absent(
        self, client, auth_headers, site, other_camera, other_zone, make_event, product
    ):
        make_event(other_camera, other_zone, type="delivery_trip", **_trip_attributes(product, 9))
        body = client.get("/api/deliveries", headers=auth_headers).json()
        assert body["trips"] == []
        assert body["totals"] == []

    def test_snapshot_url_from_clip(
        self, client, auth_headers, site, camera, zone, make_event, make_clip, product
    ):
        with_clip = make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 1))
        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 1))
        make_clip(with_clip)

        trips = client.get("/api/deliveries", headers=auth_headers).json()["trips"]
        by_id = {t["event_id"]: t for t in trips}
        assert by_id[with_clip.id]["snapshot_url"].startswith("https://fake/snapshots/")
        others = [t for t in trips if t["event_id"] != with_clip.id]
        assert others[0]["snapshot_url"] is None

    def test_summary_events_are_not_trips(
        self, client, auth_headers, site, camera, make_event, product
    ):
        make_event(camera, None, type="delivery_summary", counts={"Cola crate": 5}, trips=2)
        body = client.get("/api/deliveries", headers=auth_headers).json()
        assert body["trips"] == []


class TestSaveTripSample:

    @pytest.fixture
    def trip_with_crop(self, db, camera, zone, make_event, fake_storage):
        from app import storage

        event = make_event(
            camera,
            zone,
            type="delivery_trip",
            items=[],
            unmatched=1,
            crop_key="snapshots/t-crop.jpg",
        )
        storage.upload_bytes("snapshots/t-crop.jpg", b"crate pixels", "image/jpeg")
        return event

    def _save(self, client, headers, event_id, product_id):
        return client.post(
            f"/api/deliveries/{event_id}/sample",
            headers=headers,
            json={"product_type_id": str(product_id)},
        )

    def test_crop_becomes_a_sample(
        self, client, admin_headers, site, trip_with_crop, product, fake_storage, db
    ):
        res = self._save(client, admin_headers, trip_with_crop.id, product.id)
        assert res.status_code == 200
        assert res.json()["url"].startswith("https://fake/product-samples/")

        from app.models import ProductSample

        samples = db.query(ProductSample).filter_by(product_type_id=product.id).all()
        assert len(samples) == 1
        assert fake_storage[samples[0].storage_key] == b"crate pixels"
        assert "snapshots/t-crop.jpg" in fake_storage

    def test_crop_url_surfaces_in_listing(self, client, auth_headers, site, trip_with_crop):
        trips = client.get("/api/deliveries", headers=auth_headers).json()["trips"]
        assert trips[0]["crop_url"] == "https://fake/snapshots/t-crop.jpg"

    def test_trip_without_crop_is_404(
        self, client, admin_headers, site, camera, zone, make_event, product
    ):
        bare = make_event(camera, zone, type="delivery_trip", items=[], unmatched=0)
        assert self._save(client, admin_headers, bare.id, product.id).status_code == 404

    def test_viewer_cannot_save(
        self, client, tenant, site, trip_with_crop, product, make_access_token
    ):
        headers = {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}
        assert self._save(client, headers, trip_with_crop.id, product.id).status_code == 403

    def test_another_tenants_trip_is_404(
        self,
        client,
        site,
        second_tenant,
        other_camera,
        other_zone,
        make_event,
        product,
        make_access_token,
        fake_storage,
    ):
        from app import storage

        theirs = make_event(
            other_camera, other_zone, type="delivery_trip", crop_key="snapshots/their-crop.jpg"
        )
        storage.upload_bytes("snapshots/their-crop.jpg", b"theirs", "image/jpeg")
        their_headers = {
            "Authorization": f"Bearer {make_access_token(second_tenant, role='admin')}"
        }
        assert self._save(client, their_headers, theirs.id, product.id).status_code == 404

    def test_another_tenants_product_is_404(
        self, client, admin_headers, site, trip_with_crop, db, other_site
    ):
        from app.models import ProductType

        their_product = ProductType(site_id=other_site.id, name="Their crate")
        db.add(their_product)
        db.flush()
        assert (
            self._save(client, admin_headers, trip_with_crop.id, their_product.id).status_code
            == 404
        )

    def test_sixth_sample_rejected(self, client, admin_headers, site, trip_with_crop, product, db):
        from app.models import ProductSample

        for i in range(5):
            db.add(
                ProductSample(product_type_id=product.id, storage_key=f"product-samples/x/{i}.jpg")
            )
        db.flush()
        res = self._save(client, admin_headers, trip_with_crop.id, product.id)
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "validation_error"


class TestChatTool:
    def test_get_deliveries_tool(self, db, site, camera, zone, make_event, make_clip, product):
        from app.services.ai.tools import ToolContext, dispatch

        trip = make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 2))
        make_clip(trip)

        ctx = ToolContext(db=db, site=site, zones=[zone], tz=ZoneInfo(site.timezone))
        out = dispatch("get_deliveries", {}, ctx)

        assert out["trips"] == 1
        assert out["totals"] == [
            {"product": "Cola crate", "packages": 2, "units": 48, "unit_label": "bottles"}
        ]
        assert out["trip_details"][0]["event_id"] == trip.id
        assert trip.id in ctx.events_out
        assert ctx.events_out[trip.id]["snapshot_url"].startswith("https://fake/")

    def test_product_name_filter(self, db, site, camera, zone, make_event, product):
        from app.services.ai.tools import ToolContext, dispatch

        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 2))
        ctx = ToolContext(db=db, site=site, zones=[zone], tz=ZoneInfo(site.timezone))
        out = dispatch("get_deliveries", {"product_name": "no such product"}, ctx)
        assert out["totals"] == []
        assert out["trips"] == 1


class TestReportContext:
    def test_report_context_carries_deliveries(self, db, site, camera, zone, make_event, product):
        from app.services import analytics

        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 3))
        context = analytics.build_report_context(db, site)
        assert context["deliveries"]["trips"] == 1
        assert context["deliveries"]["products"] == [
            {"name": "Cola crate", "packages": 3, "units": 72, "unit_label": "bottles"}
        ]

    def test_fallback_report_renders_deliveries_section(
        self, db, site, camera, zone, make_event, product
    ):
        from app.services.ai import report as report_service

        make_event(camera, zone, type="delivery_trip", **_trip_attributes(product, 3))
        out = report_service.generate_report(db, site, day=None)
        assert out.generated_by == "fallback"
        assert "## Поставки" in out.markdown
        assert "Cola crate" in out.markdown
