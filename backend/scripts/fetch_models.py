import argparse
import logging
import os
import sys
from pathlib import Path

from app.config import settings

log = logging.getLogger("fetch_models")


def yolo_target() -> Path:
    configured = Path(settings.yolo_model)
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[1] / configured


def fetch_yolo() -> Path:
    target = yolo_target()
    if target.exists():
        log.info("YOLO weights already present: %s (%.1f MB)", target, target.stat().st_size / 1e6)
        return target

    log.info("Fetching YOLO weights %s …", settings.yolo_model)
    from ultralytics import YOLO

    cwd = os.getcwd()
    os.chdir(target.parent)
    try:
        YOLO(settings.yolo_model)
    finally:
        os.chdir(cwd)

    if not target.exists():
        raise SystemExit(f"ultralytics did not leave {target} where it was expected")
    log.info("YOLO weights ready: %s (%.1f MB)", target, target.stat().st_size / 1e6)
    return target


def delivery_target() -> Path:
    configured = Path(settings.delivery_model)
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[1] / configured


def fetch_delivery() -> Path | None:
    if not settings.delivery_enabled:
        log.info("Delivery counting disabled (DELIVERY_ENABLED=false) — skipping")
        return None
    target = delivery_target()
    from ultralytics import YOLO

    cwd = os.getcwd()
    os.chdir(target.parent)
    try:
        if target.exists():
            log.info(
                "Delivery weights already present: %s (%.1f MB)",
                target,
                target.stat().st_size / 1e6,
            )
        else:
            log.info("Fetching delivery weights %s …", settings.delivery_model)
        model = YOLO(settings.delivery_model)
        model.set_classes(["box"])
    finally:
        os.chdir(cwd)

    if not target.exists():
        raise SystemExit(f"ultralytics did not leave {target} where it was expected")
    log.info("Delivery weights ready: %s (%.1f MB)", target, target.stat().st_size / 1e6)
    return target


def fetch_clip() -> None:
    if not settings.clip_enabled:
        log.info("CLIP disabled (CLIP_ENABLED=false) — skipping")
        return

    log.info("Warming CLIP %s/%s …", settings.clip_model, settings.clip_pretrained)
    import open_clip

    open_clip.create_model_and_transforms(settings.clip_model, pretrained=settings.clip_pretrained)
    log.info("CLIP checkpoint ready")


def check(*, skip_clip: bool = False) -> int:
    missing = []
    target = yolo_target()
    if not target.exists():
        missing.append(f"YOLO weights {target}")

    if settings.delivery_enabled and not delivery_target().exists():
        missing.append(f"delivery (YOLO-World) weights {delivery_target()}")

    if settings.clip_enabled and not skip_clip:
        from huggingface_hub.constants import HF_HUB_CACHE

        cache = Path(HF_HUB_CACHE)
        if not cache.exists() or not any(cache.iterdir()):
            missing.append(f"CLIP checkpoint (empty HuggingFace cache at {cache})")

    for item in missing:
        print(f"missing: {item}", file=sys.stderr)
    return 1 if missing else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report missing weights, exit 1")
    parser.add_argument(
        "--skip-clip",
        action="store_true",
        help="YOLO only (~19 MB, not ~600 MB). Applies to --check as well.",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args()

    if args.check:
        return check(skip_clip=args.skip_clip)

    fetch_yolo()
    fetch_delivery()
    if not args.skip_clip:
        fetch_clip()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
