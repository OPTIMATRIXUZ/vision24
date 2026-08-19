import os
import uuid
from urllib.parse import urlparse

import pytest

DEFAULT_TEST_URL = "postgresql+psycopg://vision24:vision24@localhost:5435/vision24_test"
TEST_DATABASE_URL = os.environ.get("VISION24_TEST_DATABASE_URL", DEFAULT_TEST_URL)


def _db_name(url: str) -> str:
    return urlparse(url.replace("postgresql+psycopg://", "postgresql://")).path.lstrip("/")


def _guard_test_database() -> None:
    from app.config import settings

    app_db = _db_name(settings.database_url)
    test_db = _db_name(TEST_DATABASE_URL)
    if not test_db:
        pytest.exit(f"VISION24_TEST_DATABASE_URL has no database name: {TEST_DATABASE_URL}", 1)
    if app_db == test_db:
        pytest.exit(
            "Refusing to run: the test database and the application database are both "
            f"'{app_db}'. The test fixtures DROP this database. Set "
            "VISION24_TEST_DATABASE_URL to something else.",
            1,
        )


_guard_test_database()


@pytest.fixture(scope="session")
def test_engine():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    name = _db_name(TEST_DATABASE_URL)

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    alembic_cfg = Config("alembic.ini")
    os.environ["VISION24_ALEMBIC_URL"] = TEST_DATABASE_URL
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        os.environ.pop("VISION24_ALEMBIC_URL", None)

    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(test_engine):
    conn = test_engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
def db(db_connection):
    from sqlalchemy.orm import Session

    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def bound_sessionmaker(request, monkeypatch):
    if "db_connection" not in request.fixturenames:
        yield
        return

    import sys

    from sqlalchemy.orm import sessionmaker

    import app.db

    conn = request.getfixturevalue("db_connection")
    test_sessionmaker = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")

    monkeypatch.setattr(app.db, "SessionLocal", test_sessionmaker)
    monkeypatch.setattr(app.db, "engine", conn.engine)
    for name, module in list(sys.modules.items()):
        if not name.startswith(("app.", "worker.", "scripts.")):
            continue
        if getattr(module, "SessionLocal", None) is not None:
            monkeypatch.setattr(module, "SessionLocal", test_sessionmaker, raising=False)
    yield


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    from app import storage

    objects: dict[str, bytes] = {}

    monkeypatch.setattr(storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(storage, "probe_bucket", lambda: True)
    monkeypatch.setattr(
        storage, "upload_bytes", lambda key, data, content_type: objects.__setitem__(key, data)
    )
    monkeypatch.setattr(storage, "download_bytes", lambda key: objects[key])
    monkeypatch.setattr(storage, "object_exists", lambda key: key in objects)
    monkeypatch.setattr(storage, "remove_object", lambda key: objects.pop(key, None))
    monkeypatch.setattr(storage, "presign_get", lambda key, expires_hours=1: f"https://fake/{key}")

    def _clear_bucket() -> int:
        n = len(objects)
        objects.clear()
        return n

    monkeypatch.setattr(storage, "clear_bucket", _clear_bucket)
    monkeypatch.setattr(storage, "_presign_cache", {})
    return objects


@pytest.fixture
def tenant(db):
    from app.models import Tenant

    t = Tenant(name=f"test-{uuid.uuid4().hex[:8]}", slug="demo")
    db.add(t)
    db.flush()
    return t


@pytest.fixture
def site(db, tenant):
    return _make_site(db, tenant, "Test Site")


def _make_site(db, tenant, name: str):
    from datetime import time

    from app.models import Site

    s = Site(
        tenant_id=tenant.id,
        name=name,
        timezone="Asia/Tashkent",
        closing_time=time(21, 0),
    )
    db.add(s)
    db.flush()
    return s


@pytest.fixture
def second_tenant(db):
    from app.models import Tenant

    suffix = uuid.uuid4().hex[:8]
    t = Tenant(name=f"other-{suffix}", slug=f"other-{suffix}")
    db.add(t)
    db.flush()
    return t


@pytest.fixture
def other_site(db, second_tenant):
    return _make_site(db, second_tenant, "Other Tenant Site")


@pytest.fixture
def tenant_second_site(db, tenant):
    return _make_site(db, tenant, "Second Site")


@pytest.fixture
def make_camera(db):
    from app.models import Camera

    def _make(site, name="Camera", role="upload"):
        cam = Camera(
            site_id=site.id,
            name=name,
            rtsp_url=f"/tmp/{uuid.uuid4().hex}.mp4",
            role=role,
        )
        db.add(cam)
        db.flush()
        return cam

    return _make


@pytest.fixture
def make_zone(db):
    from app.models import Zone

    full_frame = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

    def _make(site, camera, name="Zone", kind="entrance"):
        z = Zone(
            site_id=site.id,
            camera_id=camera.id,
            name=name,
            kind=kind,
            polygon=full_frame,
            record_clips=False,
        )
        db.add(z)
        db.flush()
        return z

    return _make


@pytest.fixture
def make_event(db):
    from datetime import UTC, datetime

    from app.models import Event

    def _make(camera, zone=None, type="entry", ts=None, ts_end=None, **attributes):
        e = Event(
            camera_id=camera.id,
            zone_id=zone.id if zone else None,
            type=type,
            track_id=1,
            ts_start=ts or datetime.now(UTC),
            ts_end=ts_end,
            attributes=attributes,
        )
        db.add(e)
        db.flush()
        return e

    return _make


@pytest.fixture
def make_clip(db):
    from app.models import Clip

    def _make(event):
        c = Clip(
            event_id=event.id,
            storage_key=f"clips/{event.id}-{uuid.uuid4().hex[:8]}.mp4",
            snapshot_key=f"snapshots/{event.id}-{uuid.uuid4().hex[:8]}.jpg",
            ts_start=event.ts_start,
            duration_s=12.0,
        )
        db.add(c)
        db.flush()
        return c

    return _make


@pytest.fixture
def make_alert_rule(db):
    from app.models import AlertRule

    def _make(zone, metric="queue_len", threshold=3):
        r = AlertRule(zone_id=zone.id, metric=metric, threshold=threshold, sustain_seconds=15)
        db.add(r)
        db.flush()
        return r

    return _make


@pytest.fixture
def camera(db, site, make_camera):
    return make_camera(site, name="Mine")


@pytest.fixture
def zone(db, site, camera, make_zone):
    return make_zone(site, camera, name="My Zone")


@pytest.fixture
def other_camera(db, other_site, make_camera):
    return make_camera(other_site, name="Theirs")


@pytest.fixture
def other_zone(db, other_site, other_camera, make_zone):
    return make_zone(other_site, other_camera, name="Their Zone")


TEST_SIGNING_KEY = "conftest-signing-key-padded-well-past-the-32-byte-floor"


@pytest.fixture(autouse=True)
def auth_signing_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_secret_key", TEST_SIGNING_KEY)


@pytest.fixture
def admin_headers(tenant, make_access_token):
    return {"Authorization": f"Bearer {make_access_token(tenant, role='admin')}"}


@pytest.fixture
def make_access_token():
    from app.security import create_access_token

    def _make(tenant, *, role="owner", user_id=None, session_id=None):
        return create_access_token(
            user_id=user_id or uuid.uuid4(),
            tenant_id=tenant.id,
            session_id=session_id or uuid.uuid4(),
            role=role,
        )

    return _make


@pytest.fixture
def auth_headers(tenant, make_access_token):
    return {"Authorization": f"Bearer {make_access_token(tenant)}"}


@pytest.fixture
def access_cookie(tenant, make_access_token):
    return make_access_token(tenant)


_TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def password() -> str:
    return _TEST_PASSWORD


@pytest.fixture
def make_user(db):
    from app.models import User
    from app.security import hash_password

    def _make(tenant, *, email=None, password=_TEST_PASSWORD, role="owner", is_active=True):
        user = User(
            tenant_id=tenant.id,
            email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
        )
        db.add(user)
        db.flush()
        return user

    return _make


@pytest.fixture
def owner(db, tenant, make_user):
    return make_user(tenant, role="owner")


@pytest.fixture
def owner_headers(client, owner, sign_in):
    return {"Authorization": f"Bearer {sign_in(owner).json()['access_token']}"}


@pytest.fixture
def sign_in(client):

    def _sign_in(user, password=_TEST_PASSWORD):
        return client.post("/api/auth/login", json={"email": user.email, "password": password})

    return _sign_in


@pytest.fixture
def make_api_key(db):
    from app.models import ApiKey
    from app.security import new_api_key

    def _make(tenant, *, site=None, role="admin", **overrides):
        full, prefix, key_hash = new_api_key()
        key = ApiKey(
            tenant_id=tenant.id,
            site_id=site.id if site is not None else None,
            name="test key",
            prefix=prefix,
            key_hash=key_hash,
            role=role,
            **overrides,
        )
        db.add(key)
        db.flush()
        return full, key

    return _make


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from app.deps import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def no_real_ai_calls(monkeypatch):
    import sys

    def _refuse(*args, **kwargs):
        raise AssertionError(
            "A test tried to call a real AI provider. Stub get_provider in the test."
        )

    import app.services.ai.provider as provider_mod

    monkeypatch.setattr(provider_mod, "is_configured", lambda role="default": False)
    monkeypatch.setattr(provider_mod, "get_provider", _refuse)
    for name, module in list(sys.modules.items()):
        if not name.startswith("app.services.ai"):
            continue
        if getattr(module, "is_configured", None) is not None:
            monkeypatch.setattr(module, "is_configured", lambda role="default": False)
        if getattr(module, "get_provider", None) is not None:
            monkeypatch.setattr(module, "get_provider", _refuse)
    yield


@pytest.fixture(autouse=True)
def reset_process_state():
    yield
    from app.services.ai import chat, commentary, report

    report.clear_cache()
    chat.clear_sessions()
    commentary.reset_debounce()


@pytest.fixture(autouse=True)
def inline_jobs():
    from app.services import jobs

    jobs.set_inline_execution(True)
    try:
        yield
    finally:
        jobs.set_inline_execution(False)
