import uuid

import pytest

from app.services.ai import chat, commentary, report

pytestmark = [pytest.mark.db]


class TestReportCache:

    def test_two_sites_get_their_own_report(self, db, site, other_site, monkeypatch):

        mine = report.generate_report(db, site, day=None)
        theirs = report.generate_report(db, other_site, day=None)

        assert site.name in mine.markdown
        assert other_site.name in theirs.markdown
        assert mine.markdown != theirs.markdown

    def test_the_cache_still_caches(self, db, site, monkeypatch):
        calls = []

        def _count(context):
            calls.append(1)
            return "rendered"

        monkeypatch.setattr(report, "_render_template", _count)

        report.generate_report(db, site, day=None)
        report.generate_report(db, site, day=None)
        assert len(calls) == 1, "second call should have been served from cache"

    def test_refresh_bypasses_the_cache(self, db, site, monkeypatch):
        calls = []
        monkeypatch.setattr(report, "_render_template", lambda c: calls.append(1) or "x")

        report.generate_report(db, site, day=None)
        report.generate_report(db, site, day=None, refresh=True)
        assert len(calls) == 2

    def test_clearing_one_site_leaves_the_other_cached(self, db, site, other_site, monkeypatch):
        calls = []
        monkeypatch.setattr(report, "_render_template", lambda c: calls.append(1) or "x")

        report.generate_report(db, site, day=None)
        report.generate_report(db, other_site, day=None)
        assert len(calls) == 2

        report.clear_cache(site.id)
        report.generate_report(db, other_site, day=None)
        assert len(calls) == 2
        report.generate_report(db, site, day=None)
        assert len(calls) == 3


class TestChatSessions:

    def test_the_same_session_id_is_a_different_thread_per_tenant(self):
        a, b = uuid.uuid4(), uuid.uuid4()

        chat._get_session(a, "shared-id").messages.append("tenant A secret")
        session_b = chat._get_session(b, "shared-id")

        assert session_b.messages == [], "tenant B can read tenant A's conversation"

    def test_deleting_a_session_only_affects_that_tenant(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        chat._get_session(a, "s1").messages.append("mine")
        chat._get_session(b, "s1").messages.append("theirs")

        chat.delete_session(b, "s1")

        assert chat._get_session(a, "s1").messages == ["mine"]
        assert chat._get_session(b, "s1").messages == []

    def test_clearing_one_tenant_leaves_the_other(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        chat._get_session(a, "s1").messages.append("mine")
        chat._get_session(b, "s1").messages.append("theirs")

        chat.clear_sessions(a)

        assert chat._get_session(a, "s1").messages == []
        assert chat._get_session(b, "s1").messages == ["theirs"]

    def test_sessions_are_capped(self, monkeypatch):
        monkeypatch.setattr(chat.settings, "chat_max_sessions", 5)
        tenant_id = uuid.uuid4()

        for i in range(20):
            chat._get_session(tenant_id, f"s{i}")

        assert len(chat._sessions) <= 5

    def test_the_cap_evicts_the_least_recently_used(self, monkeypatch):
        monkeypatch.setattr(chat.settings, "chat_max_sessions", 3)
        tenant_id = uuid.uuid4()

        for i in range(3):
            chat._get_session(tenant_id, f"s{i}")
        chat._get_session(tenant_id, "s0")
        chat._get_session(tenant_id, "new")

        keys = {k[1] for k in chat._sessions}
        assert "s0" in keys
        assert "s1" not in keys


class TestCommentaryDebounce:

    def test_one_site_does_not_consume_another_sites_budget(
        self, db, site, other_site, camera, make_camera, make_event
    ):
        mine = [make_event(camera)]
        their_camera = make_camera(other_site, name="Theirs")
        theirs = [make_event(their_camera)]

        first = commentary.generate(db, site, mine)
        assert first.get("reason") != "debounce"

        second = commentary.generate(db, other_site, theirs)
        assert second.get("reason") != "debounce", "one tenant starved another's commentary"

    def test_the_debounce_still_applies_within_one_site(self, db, site, camera, make_event):
        events = [make_event(camera)]

        commentary.generate(db, site, events)
        again = commentary.generate(db, site, events)
        assert again == {"skipped": True, "reason": "debounce"}
