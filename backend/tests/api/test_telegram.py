from datetime import time as dtime

import pytest

pytestmark = [pytest.mark.db]


@pytest.fixture
def bot_token(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "telegram_bot_token", "TESTTOKEN")
    monkeypatch.setattr(settings, "telegram_chat_id", "ENV-CHAT")
    return "TESTTOKEN"


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_post(url, json=None, data=None, files=None, timeout=None):
        calls.append({"url": url, "json": json, "data": data, "files": files})

        class _Res:
            def json(self):
                return {"ok": True, "result": {"message_id": 1}}

        return _Res()

    import app.services.telegram as telegram_mod

    monkeypatch.setattr(telegram_mod.requests, "post", fake_post)
    return calls


class TestSettings:
    def test_roundtrip_persists_to_the_site_row(self, client, admin_headers, db, site):
        res = client.put(
            "/api/telegram",
            headers=admin_headers,
            json={"chat_id": "-100200", "enabled": True, "digest_time": "20:30:00"},
        )
        assert res.status_code == 200
        out = client.get("/api/telegram", headers=admin_headers).json()
        assert out["chat_id"] == "-100200"
        assert out["digest_time"] == "20:30:00"

        db.refresh(site)
        assert site.telegram_chat_id == "-100200"
        assert site.telegram_digest_time == dtime(20, 30)

    def test_viewer_cannot_update(self, client, tenant, site, make_access_token):
        headers = {"Authorization": f"Bearer {make_access_token(tenant, role='viewer')}"}
        res = client.put("/api/telegram", headers=headers, json={"chat_id": "x", "enabled": True})
        assert res.status_code == 403

    def test_another_tenants_site_is_404(self, client, admin_headers, site, other_site):
        res = client.get(f"/api/telegram?site_id={other_site.id}", headers=admin_headers)
        assert res.status_code == 404

    def test_bot_configured_reflects_env(self, client, admin_headers, site, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "telegram_bot_token", "")
        assert not client.get("/api/telegram", headers=admin_headers).json()["bot_configured"]
        monkeypatch.setattr(settings, "telegram_bot_token", "tok")
        assert client.get("/api/telegram", headers=admin_headers).json()["bot_configured"]


class TestSendTest:
    def test_uses_the_site_chat_id_not_env(self, client, admin_headers, site, bot_token, sent):
        client.put(
            "/api/telegram",
            headers=admin_headers,
            json={"chat_id": "SITE-CHAT", "enabled": True},
        )
        res = client.post("/api/telegram/test", headers=admin_headers)
        assert res.status_code == 200
        assert sent[-1]["json"]["chat_id"] == "SITE-CHAT"
        assert "botTESTTOKEN/sendMessage" in sent[-1]["url"]

    def test_env_chat_is_the_fallback(self, client, admin_headers, site, bot_token, sent):
        client.post("/api/telegram/test", headers=admin_headers)
        assert sent[-1]["json"]["chat_id"] == "ENV-CHAT"

    def test_unconfigured_is_a_400(self, client, admin_headers, site, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "telegram_bot_token", "")
        res = client.post("/api/telegram/test", headers=admin_headers)
        assert res.status_code == 400

    def test_disabled_site_refuses(self, client, admin_headers, site, bot_token, sent):
        client.put("/api/telegram", headers=admin_headers, json={"chat_id": "c", "enabled": False})
        res = client.post("/api/telegram/test", headers=admin_headers)
        assert res.status_code == 400
        assert sent == []


class TestDigest:
    def test_digest_sends_the_report_numbers(
        self, client, admin_headers, site, camera, zone, make_event, bot_token, sent
    ):
        make_event(camera, zone, type="entry")
        res = client.post("/api/telegram/digest", headers=admin_headers, json={})
        assert res.status_code == 200
        text = sent[-1]["json"]["text"]
        assert "Vision24" in text and site.name in text
        assert "1" in text

    def test_unconfigured_digest_is_400(self, client, admin_headers, site, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "telegram_bot_token", "")
        assert (
            client.post("/api/telegram/digest", headers=admin_headers, json={}).status_code == 400
        )


class TestAlertPhotoPath:

    def test_snapshot_is_found_and_downloaded(
        self, db, site, camera, zone, make_event, make_clip, fake_storage, monkeypatch
    ):
        from app import storage
        from app.services import telegram

        event = make_event(camera, zone)
        clip = make_clip(event)
        storage.upload_bytes(clip.snapshot_key, b"jpeg-bytes", "image/jpeg")
        monkeypatch.setattr(telegram, "SNAPSHOT_WAIT_S", 0.5)
        monkeypatch.setattr(telegram, "SNAPSHOT_POLL_S", 0.01)
        assert telegram._wait_for_snapshot(event.id) == b"jpeg-bytes"

    def test_missing_snapshot_times_out_to_none(self, db, site, monkeypatch):
        from app.services import telegram

        monkeypatch.setattr(telegram, "SNAPSHOT_WAIT_S", 0.05)
        monkeypatch.setattr(telegram, "SNAPSHOT_POLL_S", 0.01)
        assert telegram._wait_for_snapshot(999_999) is None

    def test_photo_goes_as_multipart_upload(self, sent):
        from app.services import telegram

        out = telegram._send_photo_sync("tok", "chat", b"jpeg", "caption")
        assert out["ok"]
        call = sent[-1]
        assert "sendPhoto" in call["url"]
        assert call["files"]["photo"][1] == b"jpeg"
        assert call["data"]["caption"] == "caption"

    def test_muted_flag_short_circuits(self, monkeypatch):
        from app.services import telegram

        spawned = []
        monkeypatch.setattr(telegram, "MUTED", True)
        monkeypatch.setattr(telegram.threading, "Thread", lambda **kw: spawned.append(kw))
        telegram.send_alert("boom", camera_id=None, event_id=None)
        assert spawned == []


class TestScheduler:
    def test_tick_sends_once_and_stamps(self, db, site, bot_token, sent, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.services import digest_scheduler

        now_local = datetime.now(ZoneInfo(site.timezone))
        site.telegram_chat_id = "SITE-CHAT"
        site.telegram_digest_time = dtime(0, 0)
        db.add(site)
        db.flush()

        digest_scheduler._tick()
        assert len(sent) == 1
        db.refresh(site)
        assert site.telegram_digest_last_sent_on == now_local.date()

        digest_scheduler._tick()
        assert len(sent) == 1
