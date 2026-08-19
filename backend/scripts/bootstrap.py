import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _say(message: str) -> None:
    print(f"bootstrap: {message}", flush=True)


def upgrade_schema() -> None:
    from alembic import command
    from alembic.config import Config

    cwd = os.getcwd()
    os.chdir(BACKEND_DIR)
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        command.upgrade(config, "head")
    finally:
        os.chdir(cwd)
    _say("schema at head")


def ensure_storage() -> None:
    from app import storage

    storage.ensure_bucket()
    _say(f"bucket {storage.settings.minio_bucket!r} ready")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        action="store_true",
        help="also create the demo tenant and site (development only)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    upgrade_schema()
    ensure_storage()

    if args.seed:
        from app import seed

        seed.run()
        _say("demo tenant and site ready")

    return 0


if __name__ == "__main__":
    sys.exit(main())
