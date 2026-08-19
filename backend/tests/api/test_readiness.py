import pytest
from sqlalchemy.exc import OperationalError

pytestmark = [pytest.mark.db]

SECRET = "connection to server at postgres-prod-7 port 5432 user vision24"


@pytest.fixture
def broken_database(monkeypatch):
    from app import db

    class DeadEngine:
        def connect(self):
            raise OperationalError(f"SELECT 1 [{SECRET}]", None, Exception(SECRET))

    monkeypatch.setattr(db, "engine", DeadEngine())


@pytest.fixture
def broken_storage(monkeypatch):
    from app import storage
    from app.errors import StorageError

    def _boom():
        raise StorageError(SECRET)

    monkeypatch.setattr(storage, "probe_bucket", _boom)


def _check(body, name) -> dict:
    return next(c for c in body["checks"] if c["name"] == name)


class TestHealthy:
    def test_all_dependencies_up_reports_ready(self, client):
        res = client.get("/api/ready")

        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ready"
        assert all(c["ok"] for c in body["checks"])

    def test_both_dependencies_are_actually_checked(self, client):
        names = {c["name"] for c in client.get("/api/ready").json()["checks"]}

        assert names == {"database", "storage"}

    def test_each_check_reports_how_long_it_took(self, client):
        for check in client.get("/api/ready").json()["checks"]:
            assert isinstance(check["latency_ms"], int | float)
            assert check["latency_ms"] >= 0


class TestDatabaseDown:
    def test_it_answers_503(self, client, broken_database):
        res = client.get("/api/ready")

        assert res.status_code == 503
        assert res.json()["status"] == "not_ready"

    def test_it_names_the_dependency_that_failed(self, client, broken_database):
        body = client.get("/api/ready").json()

        assert _check(body, "database")["ok"] is False
        assert _check(body, "database")["error"]

    def test_the_other_checks_still_run(self, client, broken_database):
        assert _check(client.get("/api/ready").json(), "storage")["ok"] is True

    def test_the_connection_detail_does_not_reach_the_caller(self, client, broken_database):
        res = client.get("/api/ready")

        assert SECRET not in res.text
        assert "OperationalError" not in res.text


class TestStorageDown:
    def test_it_answers_503(self, client, broken_storage):
        res = client.get("/api/ready")

        assert res.status_code == 503
        assert _check(res.json(), "storage")["ok"] is False

    def test_the_database_is_still_reported_up(self, client, broken_storage):
        assert _check(client.get("/api/ready").json(), "database")["ok"] is True

    def test_the_failure_message_does_not_reach_the_caller(self, client, broken_storage):
        assert SECRET not in client.get("/api/ready").text


class TestLivenessStaysSeparate:

    def test_health_is_still_ok_while_the_database_is_down(self, client, broken_database):
        res = client.get("/api/health")

        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_health_is_still_ok_while_storage_is_down(self, client, broken_storage):
        assert client.get("/api/health").status_code == 200

    def test_readiness_reflects_the_same_outage(self, client, broken_database):
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/ready").status_code == 503
