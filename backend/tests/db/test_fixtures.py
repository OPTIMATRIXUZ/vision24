import pytest
from sqlalchemy import select, text

from app.models import Camera, Tenant

pytestmark = [pytest.mark.db]


def test_schema_came_from_migrations(db):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    version = db.execute(text("select version_num from alembic_version")).scalar()
    assert version == head


def test_pgvector_is_available(db):
    installed = db.execute(
        text("select count(*) from pg_extension where extname = 'vector'")
    ).scalar()
    assert installed == 1


def test_fixtures_provide_a_tenant_and_site(db, site, tenant):
    assert site.tenant_id == tenant.id
    assert site.timezone == "Asia/Tashkent"


class TestRollbackIsolation:

    def test_writes_a_row_and_commits(self, db, site):
        db.add(Camera(site_id=site.id, name="leak-probe", rtsp_url="/tmp/x.mp4", role="upload"))
        db.commit()
        assert db.scalars(select(Camera).where(Camera.name == "leak-probe")).first() is not None

    def test_previous_commit_was_rolled_back(self, db):
        assert db.scalars(select(Camera).where(Camera.name == "leak-probe")).first() is None
        assert db.scalars(select(Tenant)).first() is None


def test_sessionlocal_is_redirected_into_the_test_transaction(db, site):
    from app.db import SessionLocal

    db.add(Camera(site_id=site.id, name="own-session", rtsp_url="/tmp/y.mp4", role="upload"))
    db.flush()

    with SessionLocal() as other:
        found = other.scalars(select(Camera).where(Camera.name == "own-session")).first()
    assert found is not None, "SessionLocal is not bound to the test transaction"


def test_http_client_reads_the_test_database_not_the_dev_one(client, admin_headers, site):
    res = client.get("/api/site", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["id"] == str(site.id)
    assert res.json()["name"] == "Test Site"


def test_every_bound_sessionmaker_points_at_the_test_transaction(db):
    import sys

    import app.db

    expected = app.db.SessionLocal
    bound = {
        name: module
        for name, module in sys.modules.items()
        if name.startswith(("app.", "worker."))
        and getattr(module, "SessionLocal", None) is not None
    }
    assert bound, "no module has SessionLocal bound — the sweep is untested"

    stragglers = [name for name, module in bound.items() if module.SessionLocal is not expected]
    assert not stragglers, f"these still point at the real database: {sorted(stragglers)}"

    assert "app.deps" in bound, "app.deps must be imported for this assertion to mean anything"


def test_storage_is_faked(fake_storage):
    from app import storage

    storage.upload_bytes("k/v.jpg", b"bytes", "image/jpeg")
    assert storage.object_exists("k/v.jpg")
    assert fake_storage["k/v.jpg"] == b"bytes"
    assert storage.presign_get("k/v.jpg").startswith("https://fake/")
