import pytest

pytestmark = [pytest.mark.db]


def _jpeg_bytes() -> bytes:
    import cv2
    import numpy as np

    img = np.full((64, 48, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _create(client, headers, name="Cola crate", **extra):
    body = {"name": name, "units_per_package": 24, "unit_label": "bottles", **extra}
    return client.post("/api/products", headers=headers, json=body)


class TestProductCrud:
    def test_create_and_list(self, client, admin_headers, site):
        res = _create(client, admin_headers)
        assert res.status_code == 200
        created = res.json()
        assert created["name"] == "Cola crate"
        assert created["units_per_package"] == 24
        assert created["samples"] == []

        listing = client.get("/api/products", headers=admin_headers).json()
        assert [p["id"] for p in listing] == [created["id"]]

    def test_update(self, client, admin_headers, site):
        pid = _create(client, admin_headers).json()["id"]
        res = client.put(
            f"/api/products/{pid}",
            headers=admin_headers,
            json={"name": "Chips box", "units_per_package": None, "unit_label": None},
        )
        assert res.status_code == 200
        assert res.json()["name"] == "Chips box"
        assert res.json()["units_per_package"] is None

    def test_duplicate_name_conflicts(self, client, admin_headers, site):
        assert _create(client, admin_headers).status_code == 200
        res = _create(client, admin_headers)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "conflict"

    def test_duplicate_name_is_case_insensitive(self, client, admin_headers, site):
        assert _create(client, admin_headers, name="Cola crate").status_code == 200
        res = _create(client, admin_headers, name="COLA CRATE")
        assert res.status_code == 409

    def test_same_name_allowed_on_another_site(
        self, client, admin_headers, site, tenant_second_site
    ):
        body = {"name": "Cola crate", "units_per_package": 24, "unit_label": "bottles"}
        first = client.post(f"/api/products?site_id={site.id}", headers=admin_headers, json=body)
        assert first.status_code == 200
        res = client.post(
            f"/api/products?site_id={tenant_second_site.id}", headers=admin_headers, json=body
        )
        assert res.status_code == 200


class TestAuthorization:
    def test_viewer_cannot_create(self, client, tenant, site, make_access_token):
        headers = {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}
        res = _create(client, headers)
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "forbidden"

    def test_viewer_can_list(self, client, tenant, site, make_access_token):
        headers = {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}
        assert client.get("/api/products", headers=headers).status_code == 200


class TestOwnership:
    def test_another_tenants_product_is_404(
        self, client, admin_headers, site, second_tenant, other_site, make_access_token
    ):
        pid = _create(client, admin_headers).json()["id"]
        other_headers = {
            "Authorization": f"Bearer {make_access_token(second_tenant, role='admin')}"
        }
        res = client.put(
            f"/api/products/{pid}",
            headers=other_headers,
            json={"name": "Hijack", "units_per_package": None, "unit_label": None},
        )
        assert res.status_code == 404
        assert client.delete(f"/api/products/{pid}", headers=other_headers).status_code == 404

    def test_listing_is_site_scoped(self, client, admin_headers, site, tenant_second_site):
        res = client.post(
            f"/api/products?site_id={site.id}",
            headers=admin_headers,
            json={"name": "Cola crate", "units_per_package": 24, "unit_label": "bottles"},
        )
        assert res.status_code == 200
        other = client.get(
            f"/api/products?site_id={tenant_second_site.id}", headers=admin_headers
        ).json()
        assert other == []


class TestSamples:
    def _upload(self, client, headers, pid, data=None, content_type="image/jpeg"):
        return client.post(
            f"/api/products/{pid}/samples",
            headers=headers,
            files={
                "file": ("crate.jpg", data if data is not None else _jpeg_bytes(), content_type)
            },
        )

    def test_upload_stores_object(self, client, admin_headers, site, fake_storage):
        pid = _create(client, admin_headers).json()["id"]
        res = self._upload(client, admin_headers, pid)
        assert res.status_code == 200
        assert res.json()["url"].startswith("https://fake/product-samples/")
        keys = [k for k in fake_storage if k.startswith(f"product-samples/{pid}/")]
        assert len(keys) == 1

        listing = client.get("/api/products", headers=admin_headers).json()
        assert len(listing[0]["samples"]) == 1

    def test_sixth_sample_rejected(self, client, admin_headers, site):
        pid = _create(client, admin_headers).json()["id"]
        for _ in range(5):
            assert self._upload(client, admin_headers, pid).status_code == 200
        res = self._upload(client, admin_headers, pid)
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "validation_error"

    def test_non_image_rejected(self, client, admin_headers, site):
        pid = _create(client, admin_headers).json()["id"]
        assert (
            self._upload(client, admin_headers, pid, content_type="text/plain").status_code == 400
        )
        assert self._upload(client, admin_headers, pid, data=b"not a jpeg").status_code == 400

    def test_delete_sample_removes_object(self, client, admin_headers, site, fake_storage):
        pid = _create(client, admin_headers).json()["id"]
        sid = self._upload(client, admin_headers, pid).json()["id"]
        assert (
            client.delete(f"/api/products/{pid}/samples/{sid}", headers=admin_headers).status_code
            == 200
        )
        assert not [k for k in fake_storage if k.startswith("product-samples/")]

    def test_delete_product_removes_objects(self, client, admin_headers, site, fake_storage):
        pid = _create(client, admin_headers).json()["id"]
        self._upload(client, admin_headers, pid)
        self._upload(client, admin_headers, pid)
        assert client.delete(f"/api/products/{pid}", headers=admin_headers).status_code == 200
        assert not [k for k in fake_storage if k.startswith("product-samples/")]
        assert client.get("/api/products", headers=admin_headers).json() == []
