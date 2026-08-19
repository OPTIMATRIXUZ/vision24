import pytest
from minio.error import MinioException, S3Error
from urllib3.exceptions import MaxRetryError, NewConnectionError

from app.errors import StorageError
from app.storage import download_bytes, object_exists, probe_bucket, remove_object

pytestmark = [pytest.mark.unit]


def unreachable() -> MaxRetryError:
    return MaxRetryError(
        pool=None,
        url="/clips?location=",
        reason=NewConnectionError(None, "Failed to establish a new connection: Connection refused"),
    )


def s3_error(code: str) -> S3Error:
    return S3Error(
        code=code,
        message="test",
        resource="/clips/x",
        request_id="r",
        host_id="h",
        response=None,
    )


class FakeClient:

    def __init__(self, exc):
        self.exc = exc

    def stat_object(self, *a, **kw):
        raise self.exc

    def remove_object(self, *a, **kw):
        raise self.exc

    def get_object(self, *a, **kw):
        raise self.exc

    def bucket_exists(self, *a, **kw):
        raise self.exc


@pytest.fixture
def raising(monkeypatch):
    from app import storage

    def _install(exc):
        monkeypatch.setattr(storage, "client", lambda: FakeClient(exc))
        monkeypatch.setattr(storage, "probe_client", lambda: FakeClient(exc))

    return _install


ABSENT = ["NoSuchKey", "NoSuchObject", "NoSuchBucket", "NotFound"]
OUTAGE = ["AccessDenied", "InternalError", "SlowDown", "RequestTimeTooSkewed"]


class TestObjectExists:
    @pytest.mark.parametrize("code", ABSENT)
    def test_a_genuine_absence_is_false(self, raising, code):
        raising(s3_error(code))
        assert object_exists("clips/x.mp4") is False

    @pytest.mark.parametrize("code", OUTAGE)
    def test_an_outage_is_not_reported_as_absence(self, raising, code):
        raising(s3_error(code))
        with pytest.raises(StorageError):
            object_exists("clips/x.mp4")

    def test_an_unreachable_server_raises(self, raising):
        raising(ConnectionRefusedError("no route to minio"))
        with pytest.raises(StorageError):
            object_exists("clips/x.mp4")

    def test_a_minio_client_error_raises(self, raising):
        raising(MinioException("client is misconfigured"))
        with pytest.raises(StorageError):
            object_exists("clips/x.mp4")


class TestRemoveObject:
    @pytest.mark.parametrize("code", ABSENT)
    def test_deleting_something_already_gone_is_fine(self, raising, code):
        raising(s3_error(code))
        remove_object("clips/x.mp4")

    @pytest.mark.parametrize("code", OUTAGE)
    def test_a_failed_delete_is_not_silently_swallowed(self, raising, code):
        raising(s3_error(code))
        with pytest.raises(StorageError):
            remove_object("clips/x.mp4")

    def test_an_unreachable_server_raises(self, raising):
        raising(ConnectionRefusedError("no route to minio"))
        with pytest.raises(StorageError):
            remove_object("clips/x.mp4")


class TestARealOutage:

    def test_object_exists(self, raising):
        raising(unreachable())
        with pytest.raises(StorageError):
            object_exists("clips/x.mp4")

    def test_remove_object(self, raising):
        raising(unreachable())
        with pytest.raises(StorageError):
            remove_object("clips/x.mp4")

    def test_download_bytes(self, raising):
        raising(unreachable())
        with pytest.raises(StorageError):
            download_bytes("clips/x.mp4")

    def test_probe_bucket(self, raising):
        raising(unreachable())
        with pytest.raises(StorageError):
            probe_bucket()


class TestTheProbeClientIsBounded:

    def _pool_kw(self, client):
        return client._http.connection_pool_kw

    def test_it_does_not_retry(self):
        from app.storage import probe_client

        assert self._pool_kw(probe_client())["retries"].total == 0

    def test_its_timeouts_are_probe_sized(self):
        from app.storage import probe_client

        timeout = self._pool_kw(probe_client())["timeout"]
        assert timeout.connect_timeout <= 2
        assert timeout.read_timeout <= 3

    def test_the_upload_client_keeps_its_patience(self):
        from app.storage import client

        assert self._pool_kw(client())["retries"].total > 0


def test_download_failures_are_typed(raising):
    raising(ConnectionRefusedError("no route to minio"))
    with pytest.raises(StorageError):
        download_bytes("clips/x.mp4")
