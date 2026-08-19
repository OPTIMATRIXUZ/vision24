import pytest

from app.main import app

pytestmark = [pytest.mark.db]

UNAUTHENTICATED: set[tuple[str, str]] = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/register"),
}

UUID0 = "00000000-0000-0000-0000-000000000000"
BODY_ROUTES: dict[tuple[str, str], dict] = {
    ("PUT", "/api/site"): {"timezone": "Asia/Tashkent", "closing_time": "21:00:00"},
    ("POST", "/api/zones"): {
        "camera_id": UUID0,
        "name": "z",
        "kind": "entrance",
        "polygon": [[0, 0], [1, 0], [1, 1]],
    },
    ("PUT", "/api/zones/{zone_id}"): {
        "camera_id": UUID0,
        "name": "z",
        "kind": "entrance",
        "polygon": [[0, 0], [1, 0], [1, 1]],
    },
    ("POST", "/api/alert-rules"): {"zone_id": UUID0, "metric": "queue_len", "threshold": 3},
    ("PUT", "/api/alert-rules/{rule_id}"): {
        "zone_id": UUID0,
        "metric": "queue_len",
        "threshold": 3,
    },
    ("POST", "/api/chat"): {"session_id": "s", "message": "hi"},
    ("POST", "/api/chat/stream"): {"session_id": "s", "message": "hi"},
    ("POST", "/api/sources/cctv"): {"rtsp_url": "rtsp://x/y", "name": "c"},
    ("POST", "/api/sources/cctv/test"): {"rtsp_url": "rtsp://x/y"},
    ("POST", "/api/sources/{camera_id}/capture"): {"duration_s": 30},
    ("POST", "/api/tts"): {"text": "hello"},
    ("POST", "/api/videos/{camera_id}/analyze"): {},
}

SKIP_BODY = {
    ("POST", "/api/sources/upload"),
    ("POST", "/api/sources/{camera_id}/reupload"),
    ("POST", "/api/videos"),
}


def _routes() -> list[tuple[str, str]]:
    out = [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api")
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    ]
    return sorted(set(out))


def _concrete(path: str) -> str:
    out = path
    while "{" in out:
        start = out.index("{")
        end = out.index("}", start)
        name = out[start + 1 : end]
        value = "s1" if "session" in name else UUID0
        out = out[:start] + value + out[end + 1 :]
    return out


ALL_ROUTES = _routes()
PROTECTED = [r for r in ALL_ROUTES if r not in UNAUTHENTICATED]


def _request(client, method: str, path: str, headers: dict | None = None):
    key = (method, path)
    kwargs: dict = {"headers": headers or {}}
    if key in BODY_ROUTES:
        kwargs["json"] = BODY_ROUTES[key]
    elif key in SKIP_BODY:
        kwargs["files"] = {"file": ("x.mp4", b"\x00", "video/mp4")}
    return client.request(method, _concrete(path), **kwargs)


def test_the_sweep_actually_discovered_the_api():
    assert len(ALL_ROUTES) > 30, f"only discovered {len(ALL_ROUTES)} routes: {ALL_ROUTES}"
    assert ("GET", "/api/sources") in ALL_ROUTES
    assert PROTECTED, "no protected routes discovered"


def test_the_unauthenticated_allowlist_matches_reality():
    missing = UNAUTHENTICATED - set(ALL_ROUTES)
    assert not missing, f"allowlisted routes that no longer exist: {sorted(missing)}"


SELF_AUTHENTICATING = {("POST", "/api/auth/refresh")}


@pytest.mark.parametrize(
    ("method", "path"),
    [r for r in PROTECTED if r not in SELF_AUTHENTICATING],
    ids=lambda v: str(v),
)
def test_no_route_401s_an_anonymous_caller(client, method, path):
    res = _request(client, method, path)
    assert res.status_code != 401, (
        f"{method} {path} returned 401 without credentials, but the MVP bypass "
        f"is supposed to resolve anonymous callers as the demo owner."
    )


@pytest.mark.parametrize(("method", "path"), PROTECTED, ids=lambda v: str(v))
def test_protected_routes_reject_a_wrong_token(client, method, path):
    res = _request(client, method, path, headers={"Authorization": "Bearer not-the-token"})
    assert res.status_code == 401


@pytest.mark.parametrize(("method", "path"), sorted(UNAUTHENTICATED), ids=lambda v: str(v))
def test_public_routes_do_not_require_a_token(client, method, path):
    res = _request(client, method, path)
    assert res.status_code != 401


def test_a_valid_token_gets_past_authentication(client, admin_headers, site):
    res = client.get("/api/site", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["id"] == str(site.id)


def test_an_anonymous_caller_acts_as_the_demo_owner(client, tenant, site):
    res = client.get("/api/site")
    assert res.status_code == 200
    assert res.json()["id"] == str(site.id)


def test_unauthenticated_errors_use_the_standard_envelope(client):
    res = client.get("/api/sources", headers={"Authorization": "Bearer not-a-credential"})
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "unauthenticated"
    assert body["error"]["request_id"] == res.headers["X-Request-ID"]
