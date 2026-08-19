import pytest

from app import storage
from app.services import jobs

pytestmark = [pytest.mark.db]


def reset(client, headers, tenant):
    return client.post("/api/reset", headers=headers, json={"confirm": tenant.slug})


def exists(db, model, row_id) -> bool:
    from sqlalchemy import select

    return db.scalars(select(model.id).where(model.id == row_id)).first() is not None


@pytest.fixture
def their_data(db, other_site, make_camera, make_event, make_clip, fake_storage):
    camera = make_camera(other_site, name="Theirs")
    event = make_event(camera)
    clip = make_clip(event)
    storage.upload_bytes(clip.storage_key, b"their clip", "video/mp4")
    storage.upload_bytes(clip.snapshot_key, b"their snap", "image/jpeg")
    storage.upload_bytes(f"processed/{camera.id}.mp4", b"their processed", "video/mp4")
    storage.upload_bytes(f"heatmaps/{camera.id}.jpg", b"their heatmap", "image/jpeg")
    return {"camera": camera, "event": event, "clip": clip}


@pytest.fixture
def running_job(db):
    from datetime import UTC, datetime

    from app.models import AnalysisJob
    from app.services.jobs import RUNTIME_ID

    def start(camera_id):
        job = AnalysisJob(
            camera_id=camera_id,
            kind="analyze",
            state="running",
            runtime_id=RUNTIME_ID,
            queued_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
        )
        db.add(job)
        db.flush()
        return job

    return start


@pytest.fixture
def my_data(db, site, camera, make_event, make_clip, fake_storage):
    event = make_event(camera)
    clip = make_clip(event)
    storage.upload_bytes(clip.storage_key, b"my clip", "video/mp4")
    storage.upload_bytes(clip.snapshot_key, b"my snap", "image/jpeg")
    storage.upload_bytes(f"processed/{camera.id}.mp4", b"my processed", "video/mp4")
    return {"camera": camera, "event": event, "clip": clip}


class TestStorageBlastRadius:
    def test_reset_does_not_touch_another_tenants_objects(
        self, client, owner_headers, tenant, db, my_data, their_data, fake_storage
    ):
        res = reset(client, owner_headers, tenant)
        assert res.status_code == 200

        their_clip = their_data["clip"]
        assert storage.object_exists(their_clip.storage_key), "another tenant's clip was deleted"
        assert storage.object_exists(their_clip.snapshot_key)
        assert storage.object_exists(f"processed/{their_data['camera'].id}.mp4")
        assert storage.object_exists(f"heatmaps/{their_data['camera'].id}.jpg")

    def test_reset_does_remove_our_own_objects(
        self, client, owner_headers, tenant, db, my_data, fake_storage
    ):
        mine = my_data["clip"]
        assert storage.object_exists(mine.storage_key)

        reset(client, owner_headers, tenant)

        assert not storage.object_exists(mine.storage_key)
        assert not storage.object_exists(mine.snapshot_key)
        assert not storage.object_exists(f"processed/{my_data['camera'].id}.mp4")

    def test_the_reported_count_reflects_what_was_actually_scoped(
        self, client, owner_headers, tenant, db, my_data, their_data, fake_storage
    ):
        before = len(fake_storage)
        res = reset(client, owner_headers, tenant)
        removed = res.json()["objects_removed"]
        assert removed > 0
        assert len(fake_storage) < before
        assert len(fake_storage) >= 4, "the other tenant's four objects must survive"


class TestRowBlastRadius:
    def test_another_tenants_rows_survive(
        self, client, owner_headers, tenant, db, my_data, their_data
    ):
        from app.models import Camera, Clip, Event

        mine = my_data["camera"].id
        theirs = {
            Camera: their_data["camera"].id,
            Event: their_data["event"].id,
            Clip: their_data["clip"].id,
        }

        res = reset(client, owner_headers, tenant)

        assert res.status_code == 200
        assert not exists(db, Camera, mine), "the reset did nothing"

        for model, row_id in theirs.items():
            assert exists(db, model, row_id), f"{model.__name__} was destroyed"

    def test_our_own_rows_are_gone(self, client, owner_headers, tenant, db, my_data):
        from app.models import Camera, Event

        camera_id, event_id = my_data["camera"].id, my_data["event"].id

        reset(client, owner_headers, tenant)

        assert not exists(db, Camera, camera_id)
        assert not exists(db, Event, event_id)


class TestProductCatalogBlastRadius:

    @pytest.fixture
    def make_product(self, db, fake_storage):
        from app.models import ProductSample, ProductType

        def _make(site, name="Cola crate"):
            product = ProductType(site_id=site.id, name=name, units_per_package=24)
            db.add(product)
            db.flush()
            key = f"product-samples/{product.id}/sample.jpg"
            db.add(ProductSample(product_type_id=product.id, storage_key=key))
            db.flush()
            storage.upload_bytes(key, b"sample photo", "image/jpeg")
            return product, key

        return _make

    def test_reset_removes_our_products_and_their_photos(
        self, client, owner_headers, tenant, db, site, make_product
    ):
        from app.models import ProductSample, ProductType

        product, key = make_product(site)

        res = reset(client, owner_headers, tenant)

        assert res.status_code == 200
        assert not exists(db, ProductType, product.id)
        assert not db.query(ProductSample).filter_by(product_type_id=product.id).first()
        assert not storage.object_exists(key)

    def test_reset_spares_another_tenants_products(
        self, client, owner_headers, tenant, db, site, other_site, make_product
    ):
        from app.models import ProductType

        product, key = make_product(other_site, name="Their crate")

        res = reset(client, owner_headers, tenant)

        assert res.status_code == 200
        assert exists(db, ProductType, product.id), "another tenant's product was destroyed"
        assert storage.object_exists(key), "another tenant's sample photo was deleted"


class TestJobScoping:
    def test_another_tenants_running_job_does_not_block_our_reset(
        self, client, owner_headers, tenant, db, site, their_data, running_job
    ):
        running_job(their_data["camera"].id)

        res = reset(client, owner_headers, tenant)
        assert res.status_code == 200, "another tenant's job blocked this reset"

    def test_our_own_running_job_still_blocks_the_reset(
        self, client, owner_headers, tenant, db, my_data, running_job
    ):
        running_job(my_data["camera"].id)

        res = reset(client, owner_headers, tenant)
        assert res.status_code == 409

    def test_reset_does_not_drop_another_tenants_job_record(
        self, client, owner_headers, tenant, db, site, their_data, running_job
    ):
        their_camera_id = their_data["camera"].id
        running_job(their_camera_id)

        res = reset(client, owner_headers, tenant)

        assert res.status_code == 200
        assert jobs.get(db, their_camera_id) is not None, "their queued job was silently dropped"


class TestJobsHelpers:

    def test_any_active_restricted_to_given_cameras(self, db, site, make_camera, running_job):
        a, b = make_camera(site, name="A"), make_camera(site, name="B")
        running_job(a.id)

        assert jobs.any_active(db, [a.id]) is True
        assert jobs.any_active(db, [b.id]) is False
        assert jobs.any_active(db) is True

    def test_clear_removes_only_the_named_cameras(self, db, site, make_camera, running_job):
        a, b = make_camera(site, name="A"), make_camera(site, name="B")
        running_job(a.id)
        running_job(b.id)

        jobs.clear(db, [a.id])

        assert jobs.get(db, a.id) is None
        assert jobs.get(db, b.id) is not None

    def test_clear_with_no_argument_still_clears_everything(
        self, db, site, make_camera, running_job
    ):
        a = make_camera(site, name="A")
        running_job(a.id)

        jobs.clear(db)

        assert jobs.get(db, a.id) is None
