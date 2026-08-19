import time
from datetime import timedelta
from io import BytesIO

from minio import Minio
from minio.error import MinioException, S3Error
from urllib3 import PoolManager, Retry, Timeout
from urllib3.exceptions import HTTPError as Urllib3Error

from app.config import settings
from app.errors import StorageError

UNREACHABLE = (MinioException, Urllib3Error, OSError)

_client: Minio | None = None
_signing_client: Minio | None = None

_presign_cache: dict[str, tuple[str, float]] = {}
_PRESIGN_REUSE_S = 45 * 60


def client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
    return _client


def signing_client() -> Minio:
    global _signing_client
    if not settings.minio_public_endpoint:
        return client()
    if _signing_client is None:
        _signing_client = Minio(
            settings.minio_public_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_public_secure,
            region=settings.minio_region,
        )
    return _signing_client


def ensure_bucket() -> None:
    c = client()
    if not c.bucket_exists(settings.minio_bucket):
        c.make_bucket(settings.minio_bucket)


_probe_client: Minio | None = None

_PROBE_CONNECT_S = 1.5
_PROBE_READ_S = 2.0


def probe_client() -> Minio:
    global _probe_client
    if _probe_client is None:
        _probe_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
            http_client=PoolManager(
                timeout=Timeout(connect=_PROBE_CONNECT_S, read=_PROBE_READ_S),
                retries=Retry(total=0, read=0, connect=0),
            ),
        )
    return _probe_client


def probe_bucket() -> bool:
    try:
        return probe_client().bucket_exists(settings.minio_bucket)
    except UNREACHABLE as exc:
        raise StorageError("Object storage is unreachable.") from exc


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    client().put_object(
        settings.minio_bucket, key, BytesIO(data), length=len(data), content_type=content_type
    )


_ABSENT_CODES = {"NoSuchKey", "NoSuchObject", "NoSuchBucket", "NotFound"}


def remove_object(key: str) -> None:
    try:
        client().remove_object(settings.minio_bucket, key)
    except S3Error as exc:
        if exc.code in _ABSENT_CODES:
            return
        raise StorageError(f"Could not delete {key} from object storage.") from exc
    except UNREACHABLE as exc:
        raise StorageError(f"Could not delete {key} from object storage.") from exc


def object_exists(key: str) -> bool:
    try:
        client().stat_object(settings.minio_bucket, key)
    except S3Error as exc:
        if exc.code in _ABSENT_CODES:
            return False
        raise StorageError(f"Could not read {key} from object storage.") from exc
    except UNREACHABLE as exc:
        raise StorageError("Object storage is unreachable.") from exc
    return True


def download_bytes(key: str) -> bytes:
    try:
        resp = client().get_object(settings.minio_bucket, key)
    except UNREACHABLE as exc:
        raise StorageError(f"Could not read {key} from object storage.") from exc
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def clear_bucket() -> int:
    c = client()
    removed = 0
    for obj in list(c.list_objects(settings.minio_bucket, recursive=True)):
        c.remove_object(settings.minio_bucket, obj.object_name)
        removed += 1
    return removed


def presign_get(key: str, expires_hours: int = 1) -> str:
    now = time.monotonic()
    cached = _presign_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]
    url = signing_client().presigned_get_object(
        settings.minio_bucket, key, expires=timedelta(hours=expires_hours)
    )
    _presign_cache[key] = (url, now + _PRESIGN_REUSE_S)
    return url
