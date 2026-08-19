import queue
import threading
import uuid

import pytest

from app.services import jobs

pytestmark = [pytest.mark.unit]


@pytest.fixture
def threaded(monkeypatch):
    monkeypatch.setattr(jobs, "_inline", False)
    monkeypatch.setattr(jobs, "_queue", queue.Queue())
    monkeypatch.setattr(jobs, "_thread", None)
    monkeypatch.setattr(jobs, "_pending", {})

    ran: list[uuid.UUID] = []
    seen = threading.Event()

    def fake_execute(job_id):
        ran.append(job_id)
        seen.set()

    monkeypatch.setattr(jobs, "_execute", fake_execute)
    return ran, seen


def test_the_consumer_thread_runs_a_queued_job(threaded):
    ran, seen = threaded
    job_id = uuid.uuid4()

    jobs._queue.put(job_id)
    jobs._ensure_worker()

    assert seen.wait(timeout=5), "the consumer thread never picked the job up"
    assert ran == [job_id]


def test_the_thread_survives_a_job_that_raises(threaded, monkeypatch):
    ran, _ = threaded
    done = threading.Event()

    first, second = uuid.uuid4(), uuid.uuid4()

    def fake_execute(job_id):
        if job_id == first:
            raise RuntimeError("boom")
        ran.append(job_id)
        done.set()

    monkeypatch.setattr(jobs, "_execute", fake_execute)

    jobs._queue.put(first)
    jobs._queue.put(second)
    jobs._ensure_worker()

    assert done.wait(timeout=5), "the thread died on the first job"
    assert ran == [second]


def test_ensure_worker_does_not_start_a_second_thread(threaded):
    jobs._ensure_worker()
    first = jobs._thread

    jobs._ensure_worker()

    assert jobs._thread is first
    assert first.is_alive()


def test_ensure_worker_replaces_a_dead_thread(threaded, monkeypatch):
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    monkeypatch.setattr(jobs, "_thread", dead)

    jobs._ensure_worker()

    assert jobs._thread is not dead
    assert jobs._thread.is_alive()


def test_the_runtime_id_is_random_not_derived_from_the_pid():
    import os
    import subprocess
    import sys

    assert len(jobs.RUNTIME_ID) == 32
    assert str(os.getpid()) not in jobs.RUNTIME_ID

    other = subprocess.run(
        [sys.executable, "-c", "from app.services import jobs; print(jobs.RUNTIME_ID)"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout.strip()
    assert other != jobs.RUNTIME_ID
