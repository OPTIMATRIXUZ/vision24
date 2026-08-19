import argparse
import logging
import os
import time as time_mod

from app.config import settings
from app.logging_config import configure_logging
from app.services import locks
from worker.supervisor import Supervisor

configure_logging(
    "worker-supervisor",
    level=settings.log_level,
    fmt=settings.log_format,
    log_dir=settings.log_path,
)
log = logging.getLogger("worker")


def _acquire_singleton_lock() -> None:
    if not locks.try_acquire(locks.supervisor_lock_name()):
        log.info("Another supervisor already holds the detection lock — exiting duplicate")
        os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vision 24 detection worker")
    parser.add_argument("--camera", help="restrict to one camera (name substring or id)")
    parser.add_argument("--show", action="store_true", help="draw boxes in a debug window")
    args = parser.parse_args()

    _acquire_singleton_lock()

    supervisor = Supervisor(show=args.show, only=args.camera)
    if args.show and len(supervisor.wanted()) > 1:
        raise SystemExit("--show needs a single camera: pass --camera <name>")
    supervisor.run()


if __name__ == "__main__":
    while True:
        try:
            main()
            break
        except KeyboardInterrupt:
            break
        except SystemExit:
            raise
        except Exception:
            log.exception("Supervisor crashed, restarting in 3s")
            time_mod.sleep(3)
