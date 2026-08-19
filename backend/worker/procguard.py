import os

OWNER_PID = os.getpid()


def is_forked_child() -> bool:
    return os.getpid() != OWNER_PID
