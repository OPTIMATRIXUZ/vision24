import hashlib
import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db import engine

log = logging.getLogger(__name__)

_held: dict[str, Connection] = {}


def _key(name: str) -> int:
    digest = hashlib.blake2b(name.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def try_acquire(name: str) -> bool:
    if name in _held:
        return True

    conn = engine.connect()
    try:
        acquired = bool(
            conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _key(name)}).scalar()
        )
    except Exception:
        conn.close()
        raise

    if not acquired:
        conn.close()
        return False

    _held[name] = conn
    return True


def release(name: str) -> None:
    conn = _held.pop(name, None)
    if conn is None:
        return
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _key(name)})
    except Exception:  # noqa: BLE001 - shutting down; the session ending frees it anyway
        log.debug("Advisory unlock of %s failed; session close will release it", name)
    finally:
        conn.close()


def supervisor_lock_name() -> str:
    return "vision24:worker-supervisor"


def camera_lock_name(camera_id) -> str:
    return f"vision24:camera:{camera_id}"
