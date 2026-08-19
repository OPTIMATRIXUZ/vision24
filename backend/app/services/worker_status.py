import json
import os
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings

STATUS_PATH: Path = settings.media_path / "worker-status.json"
STATS_DIR: Path = settings.media_path / "worker-stats"
HEARTBEAT_S = 5.0
STALE_AFTER_S = 20.0


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write(cameras: list[dict]) -> None:
    _write_json(
        STATUS_PATH,
        {
            "updated_at": _now(),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "cameras": cameras,
        },
    )


def clear() -> None:
    STATUS_PATH.unlink(missing_ok=True)


def read() -> dict:
    raw = _read_json(STATUS_PATH)
    if raw is None:
        return {"running": False, "updated_at": None, "cameras": []}

    age = age_s(raw.get("updated_at"))
    running = age is not None and age <= STALE_AFTER_S and _writer_alive(raw)
    return {
        "running": running,
        "updated_at": raw.get("updated_at"),
        "cameras": raw.get("cameras", []) if running else [],
    }


def stats_path(camera_id) -> Path:
    return STATS_DIR / f"{camera_id}.json"


def write_camera_stats(camera_id, stats: dict) -> None:
    _write_json(stats_path(camera_id), {**stats, "updated_at": _now()})


def read_camera_stats(camera_id) -> dict | None:
    raw = _read_json(stats_path(camera_id))
    if raw is None:
        return None
    age = age_s(raw.get("updated_at"))
    return raw if age is not None and age <= STALE_AFTER_S else None


def clear_camera_stats(camera_id) -> None:
    stats_path(camera_id).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def _writer_alive(raw: dict) -> bool:
    if raw.get("host") not in (None, socket.gethostname()):
        return True
    return pid_alive(raw.get("pid"))


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError):
        return True
    return True
