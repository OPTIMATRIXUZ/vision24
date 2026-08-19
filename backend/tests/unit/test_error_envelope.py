import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import exception_handlers, middleware
from app.errors import JobBusyError, NotFoundError
from app.middleware import HEADER

pytestmark = pytest.mark.unit

SECRET = "sk-live-should-never-be-shown"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    middleware.register(app)
    exception_handlers.register(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError(f"internal detail with {SECRET}")

    @app.get("/missing")
    def missing():
        raise NotFoundError("No such camera.")

    @app.get("/busy")
    def busy():
        raise JobBusyError()

    @app.get("/http-error")
    def http_error():
        raise HTTPException(404, "Classic HTTPException")

    @app.get("/needs-param")
    def needs_param(count: int):
        return {"count": count}

    @app.get("/whoami")
    def whoami():
        from app.logging_config import request_id_var

        return {"seen": request_id_var.get()}

    return TestClient(app, raise_server_exceptions=False)


def test_domain_error_maps_to_its_status_and_code(client):
    r = client.get("/missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert r.json()["error"]["message"] == "No such camera."


def test_subclass_keeps_its_own_code_and_default_message(client):
    r = client.get("/busy")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "job_busy"
    assert "queued or running" in r.json()["error"]["message"]


def test_unhandled_exception_does_not_leak_internals(client):
    r = client.get("/boom")
    assert r.status_code == 500
    assert SECRET not in r.text
    assert "RuntimeError" not in r.text
    assert r.json()["error"]["code"] == "internal_error"


def test_plain_http_exception_gets_the_same_envelope(client):
    r = client.get("/http-error")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert r.json()["error"]["message"] == "Classic HTTPException"


def test_validation_errors_report_the_offending_fields(client):
    r = client.get("/needs-param")
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error"
    assert any("count" in f["loc"] for f in body["details"]["fields"])


def test_request_id_is_returned_and_matches_the_body(client):
    r = client.get("/missing")
    assert r.headers[HEADER]
    assert r.json()["error"]["request_id"] == r.headers[HEADER]


def test_a_client_supplied_request_id_is_honoured(client):
    r = client.get("/missing", headers={HEADER: "trace-abc-123"})
    assert r.headers[HEADER] == "trace-abc-123"
    assert r.json()["error"]["request_id"] == "trace-abc-123"


def test_detail_mirrors_message_for_the_current_frontend(client):
    r = client.get("/missing")
    assert r.json()["detail"] == r.json()["error"]["message"]


def test_successful_responses_also_carry_a_request_id(client):
    r = client.get("/needs-param?count=2")
    assert r.status_code == 200
    assert r.headers[HEADER]


def test_the_id_is_bound_for_anything_logging_during_the_request(client):
    r = client.get("/whoami", headers={HEADER: "trace-bound"})
    assert r.json()["seen"] == "trace-bound"


def test_the_id_does_not_leak_between_requests(client):
    from app.logging_config import request_id_var

    client.get("/whoami", headers={HEADER: "trace-first"})
    assert request_id_var.get() == "-"
