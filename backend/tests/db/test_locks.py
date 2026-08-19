import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from app.services import locks

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def release_everything():
    yield
    for name in list(locks._held):
        locks.release(name)


def test_the_second_caller_is_refused():
    name = f"vision24:test:{uuid.uuid4()}"
    assert locks.try_acquire(name) is True

    with engine.connect() as other:
        held_elsewhere = other.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": locks._key(name)}
        ).scalar()
    assert held_elsewhere is False


def test_releasing_lets_the_next_caller_in():
    name = f"vision24:test:{uuid.uuid4()}"
    assert locks.try_acquire(name) is True
    locks.release(name)

    with engine.connect() as other:
        got = other.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": locks._key(name)}
        ).scalar()
        assert got is True
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": locks._key(name)})


def test_different_cameras_do_not_block_each_other():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert locks.try_acquire(locks.camera_lock_name(a)) is True
    assert locks.try_acquire(locks.camera_lock_name(b)) is True


def test_keys_are_stable_across_processes():
    camera = uuid.uuid4()
    expected = locks._key(locks.camera_lock_name(camera))

    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from app.services import locks;print(locks._key(locks.camera_lock_name('{camera}')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(out.stdout.strip()) == expected


def test_the_key_fits_in_a_postgres_bigint():
    for name in ["vision24:worker-supervisor", locks.camera_lock_name(uuid.uuid4()), "x" * 500]:
        assert -(2**63) <= locks._key(name) < 2**63
