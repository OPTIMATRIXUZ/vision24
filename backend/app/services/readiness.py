import logging
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db, storage
from app.config import settings
from app.errors import StorageError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    latency_ms: float
    error: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"name": self.name, "ok": self.ok, "latency_ms": self.latency_ms}
        if self.error is not None:
            out["error"] = self.error
        return out


def _timed(name: str, probe) -> CheckResult:
    started = time.perf_counter()
    try:
        probe()
    except (SQLAlchemyError, StorageError, OSError) as exc:
        elapsed = (time.perf_counter() - started) * 1000
        log.warning("Readiness check %r failed after %.0f ms: %s", name, elapsed, exc)
        return CheckResult(name, ok=False, latency_ms=round(elapsed, 1), error="unreachable")
    elapsed = (time.perf_counter() - started) * 1000
    return CheckResult(name, ok=True, latency_ms=round(elapsed, 1))


def _check_database() -> None:
    with db.engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _check_storage() -> None:
    if not storage.probe_bucket():
        raise StorageError(f"Bucket {settings.minio_bucket!r} does not exist.")


def run_checks() -> list[CheckResult]:
    return [
        _timed("database", _check_database),
        _timed("storage", _check_storage),
    ]


def report() -> tuple[dict, bool]:
    checks = run_checks()
    ready = all(c.ok for c in checks)
    return {
        "status": "ready" if ready else "not_ready",
        "checks": [c.as_dict() for c in checks],
    }, ready
