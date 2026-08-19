from pathlib import Path

import pytest

from scripts import fetch_models

pytestmark = pytest.mark.unit


class TestTargetResolution:
    def test_a_bare_filename_becomes_an_absolute_path(self, monkeypatch):
        monkeypatch.setattr(fetch_models.settings, "yolo_model", "yolo11s.pt")
        target = fetch_models.yolo_target()
        assert target.is_absolute()
        assert target.name == "yolo11s.pt"
        assert (target.parent / "worker" / "detector.py").is_file()

    def test_an_absolute_path_is_taken_as_given(self, monkeypatch):
        monkeypatch.setattr(fetch_models.settings, "yolo_model", "/models/yolo11m.pt")
        assert fetch_models.yolo_target() == Path("/models/yolo11m.pt")

    def test_it_agrees_with_what_the_detector_would_load(self, monkeypatch):
        from worker.supervisor import BACKEND_DIR

        monkeypatch.setattr(fetch_models.settings, "yolo_model", "yolo11s.pt")
        assert fetch_models.yolo_target() == BACKEND_DIR / "yolo11s.pt"


class TestCheck:
    def test_reports_missing_yolo_weights(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(fetch_models.settings, "yolo_model", str(tmp_path / "absent.pt"))
        monkeypatch.setattr(fetch_models.settings, "clip_enabled", False)

        assert fetch_models.check() == 1
        assert "absent.pt" in capsys.readouterr().err

    def test_passes_when_the_weights_are_there(self, monkeypatch, tmp_path):
        weights = tmp_path / "present.pt"
        weights.write_bytes(b"not really a model, but it exists")
        monkeypatch.setattr(fetch_models.settings, "yolo_model", str(weights))
        monkeypatch.setattr(fetch_models.settings, "clip_enabled", False)

        assert fetch_models.check() == 0

    def test_skip_clip_applies_to_the_check_too(self, monkeypatch, tmp_path):
        weights = tmp_path / "present.pt"
        weights.write_bytes(b"x")
        monkeypatch.setattr(fetch_models.settings, "yolo_model", str(weights))
        monkeypatch.setattr(fetch_models.settings, "clip_enabled", True)

        assert fetch_models.check(skip_clip=True) == 0

    def test_clip_is_only_checked_when_enabled(self, monkeypatch, tmp_path):
        weights = tmp_path / "present.pt"
        weights.write_bytes(b"x")
        monkeypatch.setattr(fetch_models.settings, "yolo_model", str(weights))
        monkeypatch.setattr(fetch_models.settings, "clip_enabled", False)
        assert fetch_models.check() == 0


class TestFlagSurface:
    def test_check_does_not_download(self, monkeypatch, tmp_path):
        called = []
        monkeypatch.setattr(fetch_models, "fetch_yolo", lambda: called.append("yolo"))
        monkeypatch.setattr(fetch_models, "fetch_clip", lambda: called.append("clip"))
        monkeypatch.setattr(fetch_models.settings, "yolo_model", str(tmp_path / "absent.pt"))
        monkeypatch.setattr(fetch_models.settings, "clip_enabled", False)
        monkeypatch.setattr("sys.argv", ["fetch_models", "--check"])

        assert fetch_models.main() == 1
        assert called == []

    def test_check_passes_skip_clip_through(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(fetch_models, "check", lambda **kw: seen.update(kw) or 0)
        monkeypatch.setattr("sys.argv", ["fetch_models", "--check", "--skip-clip"])

        assert fetch_models.main() == 0
        assert seen == {"skip_clip": True}

    def test_skip_clip_fetches_only_yolo(self, monkeypatch):
        called = []
        monkeypatch.setattr(fetch_models, "fetch_yolo", lambda: called.append("yolo"))
        monkeypatch.setattr(fetch_models, "fetch_clip", lambda: called.append("clip"))
        monkeypatch.setattr("sys.argv", ["fetch_models", "--skip-clip"])

        assert fetch_models.main() == 0
        assert called == ["yolo"]

    def test_the_default_run_fetches_both(self, monkeypatch):
        called = []
        monkeypatch.setattr(fetch_models, "fetch_yolo", lambda: called.append("yolo"))
        monkeypatch.setattr(fetch_models, "fetch_clip", lambda: called.append("clip"))
        monkeypatch.setattr("sys.argv", ["fetch_models"])

        assert fetch_models.main() == 0
        assert called == ["yolo", "clip"]


def test_the_gitignore_claim_is_now_true():
    from app.config import REPO_ROOT

    gitignore = (REPO_ROOT / ".gitignore").read_text()
    referenced = "backend/scripts/fetch_models.py"
    assert referenced in gitignore
    assert (REPO_ROOT / referenced).is_file()
