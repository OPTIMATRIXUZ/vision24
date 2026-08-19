import pytest

pytestmark = [pytest.mark.db]

SECRET = "sk-proj-DO-NOT-LEAK-THIS-abcdef123456"


def exploding(*args, **kwargs):
    raise RuntimeError(
        f"Connection to https://api.example.com/v1/chat failed. "
        f"Authorization: Bearer {SECRET}. Body: {{'system': 'you are ...'}}"
    )


class TestChat:
    def test_a_provider_failure_does_not_publish_its_message(
        self, client, owner_headers, site, monkeypatch
    ):
        from app.services.ai import chat as chat_service

        monkeypatch.setattr(chat_service, "run_chat", exploding)

        res = client.post(
            "/api/chat", headers=owner_headers, json={"session_id": "s", "message": "hi"}
        )

        assert res.status_code == 502
        assert SECRET not in res.text
        assert "api.example.com" not in res.text
        assert "system" not in res.text

    def test_the_envelope_still_carries_a_request_id_to_look_it_up_by(
        self, client, owner_headers, site, monkeypatch
    ):
        from app.services.ai import chat as chat_service

        monkeypatch.setattr(chat_service, "run_chat", exploding)

        res = client.post(
            "/api/chat", headers=owner_headers, json={"session_id": "s", "message": "hi"}
        )

        assert res.json()["error"]["request_id"] == res.headers["X-Request-ID"]

    def test_the_stream_does_not_publish_it_either(self, client, owner_headers, site, monkeypatch):
        from app.services.ai import chat as chat_service

        monkeypatch.setattr(chat_service, "run_chat_stream", exploding)

        res = client.post(
            "/api/chat/stream", headers=owner_headers, json={"session_id": "s", "message": "hi"}
        )

        assert SECRET not in res.text
        assert "api.example.com" not in res.text


def test_report_generation_failure_does_not_publish_its_message(
    client, owner_headers, site, monkeypatch
):
    from app.services.ai import report as report_service

    monkeypatch.setattr(report_service, "generate_report", exploding)

    res = client.get("/api/report", headers=owner_headers)

    assert res.status_code == 502
    assert SECRET not in res.text


def test_tts_failure_does_not_publish_its_message(client, owner_headers, site, monkeypatch):
    from app.services import tts as tts_service

    monkeypatch.setattr(tts_service, "is_enabled", lambda: True)
    monkeypatch.setattr(tts_service, "synthesize", exploding)

    res = client.post("/api/tts", headers=owner_headers, json={"text": "hello"})

    assert res.status_code == 503
    assert SECRET not in res.text


def test_a_tool_failure_does_not_publish_its_message_to_the_model(monkeypatch):
    from app.services.ai import tools

    monkeypatch.setitem(tools._HANDLERS, "get_metrics", exploding)

    result = tools.dispatch("get_metrics", {}, ctx=None)

    assert SECRET not in str(result)
    assert "RuntimeError" in result["error"], "the type is still useful to the model"


def test_an_unhandled_error_never_echoes_the_exception(client, owner_headers, site, monkeypatch):
    from app.services import analytics

    monkeypatch.setattr(analytics, "get_live_metrics", exploding)

    res = client.get("/api/metrics/live", headers=owner_headers)

    assert res.status_code == 500
    assert SECRET not in res.text
