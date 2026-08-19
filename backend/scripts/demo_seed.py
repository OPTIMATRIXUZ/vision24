import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import seed as base_seed
from app.config import settings
from app.db import SessionLocal
from app.errors import ConflictError
from app.models import Camera, Event, Site, Tenant, Zone
from app.routers.zones import create_zone_with_rule
from app.services import accounts, telegram

DEMO_EMAIL = "demo@vision24.uz"
DEMO_ZONES = Path(__file__).resolve().parents[2] / "media" / "demo_zones.json"
CCTV_CAMERAS = [
    ("Cam 1 — вход", "rtsp://127.0.0.1:8554/cam1"),
    ("Cam 2 — касса", "rtsp://127.0.0.1:8554/cam2"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", "vision24demo"))
    parser.add_argument("--ends-at", help="ISO time the footage 'ends at' (site tz); empty = now")
    parser.add_argument("--skip-analysis", action="store_true")
    parser.add_argument("--skip-pos", action="store_true")
    args = parser.parse_args()

    base_seed.run()

    sample = settings.media_path / "sample.mp4"
    if not sample.exists():
        print(f"Missing {sample} — run ./media/download_sample.sh first.")
        return 2

    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant).where(Tenant.slug == "demo")).one()
        site = db.scalars(
            select(Site).where(Site.tenant_id == tenant.id).order_by(Site.created_at, Site.id)
        ).first()

        try:
            accounts.create_user(
                db, tenant.id, email=DEMO_EMAIL, password=args.password, role="owner"
            )
            print(f"Owner created: {DEMO_EMAIL} / {args.password}")
        except ConflictError:
            print(f"Owner exists: {DEMO_EMAIL}")

        upload_cam = db.scalars(
            select(Camera).where(Camera.site_id == site.id, Camera.name == "Демо-видео")
        ).first()
        if upload_cam is None:
            upload_cam = Camera(
                site_id=site.id, name="Демо-видео", rtsp_url=str(sample), role="upload"
            )
            db.add(upload_cam)
            db.flush()
            print("Upload source created: Демо-видео")
        else:
            upload_cam.rtsp_url = str(sample)

        existing_zones = {
            z.name for z in db.scalars(select(Zone).where(Zone.camera_id == upload_cam.id))
        }
        for z in json.loads(DEMO_ZONES.read_text()):
            if z["name"] in existing_zones:
                continue
            create_zone_with_rule(
                db,
                site_id=site.id,
                camera_id=upload_cam.id,
                name=z["name"],
                kind=z.get("kind", "custom"),
                polygon=z["polygon"],
                record_clips=z.get("record_clips", False),
            )
            print(f"Zone created: {z['name']} ({z.get('kind')})")

        for name, url in CCTV_CAMERAS:
            if not db.scalars(
                select(Camera).where(Camera.site_id == site.id, Camera.name == name)
            ).first():
                db.add(Camera(site_id=site.id, name=name, rtsp_url=url, role="cctv"))
                print(f"CCTV camera registered: {name} -> {url}")
        db.commit()
        site_id, upload_cam_id = site.id, upload_cam.id
        site_tz = ZoneInfo(site.timezone)

    if not args.skip_analysis:
        anchor = None
        if args.ends_at:
            anchor = datetime.fromisoformat(args.ends_at)
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=site_tz)
        telegram.MUTED = True
        try:
            from worker.batch import analyze_video

            print("Analyzing sample footage (real pipeline — takes a while on CPU)…")
            result = analyze_video(
                upload_cam_id,
                str(sample),
                progress_cb=lambda p, n: print(f"  …{p:.0%} ({n} events)", end="\r", flush=True),
                anchor_end=anchor,
            )
            print(f"\nAnalysis done: {result}")
        finally:
            telegram.MUTED = False

    with SessionLocal() as db:
        site = db.get(Site, site_id)
        if not args.skip_pos and not args.skip_analysis:
            from app.services import pos

            out = pos.simulate_day(db, site)
            print(f"POS feed simulated: {out['receipts']} receipts, planted {out['planted']}")

        from app.services import aggregates

        aggregates.compute_daily_stats_safe(db, site)

        events = db.scalar(
            select(Event.id).where(
                Event.camera_id.in_(select(Camera.id).where(Camera.site_id == site.id))
            )
        )
        print("\n=== Demo ready ===")
        print(f"  login     : {DEMO_EMAIL} / {args.password}")
        print("  frontend  : http://localhost:3001")
        print("  api       : http://localhost:8020/api/health")
        print(f"  events    : {'yes' if events else 'none (run without --skip-analysis)'}")
        print("  next      : dashboard → /pos → ask AI → /live (Replay)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
