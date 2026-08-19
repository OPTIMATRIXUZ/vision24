import socket
from pathlib import Path

import pytest

from app.config import REPO_ROOT, Settings
from app.services import worker_status
from app.storage import presign_get as real_presign_get

pytestmark = pytest.mark.unit


class TestDataDir:
    def test_defaults_to_the_checkout(self):
        settings = Settings(data_dir="")
        assert settings.data_path == REPO_ROOT
        assert settings.media_path == REPO_ROOT / "media"
        assert settings.log_path == REPO_ROOT / "logs"

    def test_relocates_everything_under_one_root(self):
        settings = Settings(data_dir="/data")
        assert settings.media_path == Path("/data/media")
        assert settings.log_path == Path("/data/logs")

    def test_log_dir_still_wins_when_set_explicitly(self):
        settings = Settings(data_dir="/data", log_dir="/var/log/vision24")
        assert settings.log_path == Path("/var/log/vision24")
        assert settings.media_path == Path("/data/media")


class TestSupervisorWorkingDirectory:

    def test_is_a_real_directory_containing_the_package(self):
        from worker.supervisor import BACKEND_DIR

        assert (BACKEND_DIR / "worker" / "camera_main.py").is_file()
        assert (BACKEND_DIR / "app" / "config.py").is_file()

    def test_is_derived_from_this_module_rather_than_the_repo_root(self):
        import worker.supervisor

        source = Path(worker.supervisor.__file__).read_text()
        assignment = next(line for line in source.splitlines() if line.startswith("BACKEND_DIR"))
        assert "REPO_ROOT" not in assignment, assignment
        assert "__file__" in assignment, assignment


class TestPresignedUrlHost:
    def test_public_endpoint_is_optional(self):
        assert Settings().minio_public_endpoint == ""

    def test_presigning_uses_the_public_endpoint_when_set(self, monkeypatch):
        from app import storage

        monkeypatch.setattr(storage.settings, "minio_endpoint", "minio:9000")
        monkeypatch.setattr(storage.settings, "minio_public_endpoint", "browser-only.invalid:9000")
        monkeypatch.setattr(storage, "_client", None)
        monkeypatch.setattr(storage, "_signing_client", None)
        monkeypatch.setattr(storage, "_presign_cache", {})

        url = real_presign_get("frame.jpg")
        assert url.startswith("http://browser-only.invalid:9000/")
        assert "minio:9000" not in url

    def test_one_client_when_no_public_endpoint_is_configured(self, monkeypatch):
        from app import storage

        monkeypatch.setattr(storage.settings, "minio_public_endpoint", "")
        monkeypatch.setattr(storage, "_client", None)
        monkeypatch.setattr(storage, "_signing_client", None)
        assert storage.signing_client() is storage.client()


class TestWorkerLiveness:
    def test_a_pid_from_another_host_is_not_probed(self):
        foreign = {"pid": 2**22, "host": "some-other-container"}
        assert worker_status._writer_alive(foreign) is True

    def test_a_dead_pid_on_this_host_is_still_caught(self):
        dead = {"pid": 2**22, "host": socket.gethostname()}
        assert worker_status._writer_alive(dead) is False

    def test_a_snapshot_without_a_host_falls_back_to_the_pid(self):
        assert worker_status._writer_alive({"pid": 2**22}) is False

    def test_write_records_the_host(self, tmp_path, monkeypatch):
        monkeypatch.setattr(worker_status, "STATUS_PATH", tmp_path / "worker-status.json")
        worker_status.write([])
        raw = worker_status._read_json(worker_status.STATUS_PATH)
        assert raw["host"] == socket.gethostname()
