import argparse
import json
import sys
import time
from pathlib import Path

from sqlalchemy import delete, select, text

from app.db import SessionLocal
from app.models import Alert, AlertRule, Camera, Clip, Embedding, Event, Site, Zone
from worker.batch import analyze_video


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--zones", required=True)
    parser.add_argument("--expected")
    args = parser.parse_args()

    video = Path(args.video).resolve()
    if not video.exists():
        print(f"No such video: {video}")
        return 2
    zones_cfg = json.loads(Path(args.zones).read_text())

    with SessionLocal() as db:
        site = db.scalars(select(Site)).first()
        if site is None:
            print("No site — run `python -m app.seed` first")
            return 2
        camera = Camera(site_id=site.id, name="__eval__", rtsp_url=str(video), role="upload")
        db.add(camera)
        db.flush()
        for z in zones_cfg:
            db.add(
                Zone(
                    site_id=site.id,
                    camera_id=camera.id,
                    name=z["name"],
                    kind=z.get("kind", "custom"),
                    polygon=z["polygon"],
                    record_clips=z.get("record_clips", False),
                )
            )
        db.commit()
        camera_id = camera.id

    try:
        t0 = time.monotonic()
        result = analyze_video(camera_id, str(video))
        wall = time.monotonic() - t0
        metrics = _collect(camera_id)
        metrics["analysis_wall_s"] = round(wall, 1)
        metrics["video_duration_s"] = round(result["duration_s"], 1)

        print("\n=== Evaluation metrics ===")
        for key, value in metrics.items():
            print(f"  {key:22} {value}")

        if not args.expected:
            print("\n(no --expected file — metrics printed only)")
            return 0

        expected = json.loads(Path(args.expected).read_text())
        failures = []
        for key, (lo, hi) in expected.items():
            value = metrics.get(key)
            status = "OK" if value is not None and lo <= value <= hi else "FAIL"
            if status == "FAIL":
                failures.append(key)
            print(f"  {status:4} {key}: {value} (expected {lo}..{hi})")
        print("\nRESULT:", "PASS" if not failures else f"FAIL ({', '.join(failures)})")
        return 0 if not failures else 1
    finally:
        _cleanup(camera_id)


def _collect(camera_id) -> dict:
    with SessionLocal() as db:

        def scalar(sql: str):
            return db.execute(text(sql), {"cam": str(camera_id)}).scalar()

        return {
            "entries_total": scalar(
                "select count(*) from event where camera_id=:cam and type='entry'"
            ),
            "unique_tracks": scalar(
                "select count(distinct track_id) from event where camera_id=:cam and type='entry'"
            ),
            "peak_occupancy": scalar(
                "select coalesce(max((attributes->>'count')::int),0) from event "
                "where camera_id=:cam and type='occupancy' and zone_id is null"
            ),
            "dwell_events": scalar(
                "select count(*) from event where camera_id=:cam and type='dwell'"
            ),
            "avg_dwell_s": float(
                scalar(
                    "select coalesce(round(avg((attributes->>'dwell_s')::float)),0) from event "
                    "where camera_id=:cam and type='dwell'"
                )
            ),
            "truncated_dwells": scalar(
                "select count(*) from event where camera_id=:cam and type='dwell' "
                "and attributes->>'truncated'='true'"
            ),
        }


def _cleanup(camera_id) -> None:
    from app import storage

    with SessionLocal() as db:
        event_ids = select(Event.id).where(Event.camera_id == camera_id)
        for clip in db.scalars(select(Clip).where(Clip.event_id.in_(event_ids))):
            if clip.storage_key:
                storage.remove_object(clip.storage_key)
            if clip.snapshot_key:
                storage.remove_object(clip.snapshot_key)
        storage.remove_object(f"processed/{camera_id}.mp4")
        storage.remove_object(f"heatmaps/{camera_id}.jpg")
        zone_ids = select(Zone.id).where(Zone.camera_id == camera_id)
        db.execute(delete(Alert).where(Alert.event_id.in_(event_ids)))
        db.execute(
            delete(Alert).where(
                Alert.rule_id.in_(select(AlertRule.id).where(AlertRule.zone_id.in_(zone_ids)))
            )
        )
        db.execute(delete(Embedding).where(Embedding.event_id.in_(event_ids)))
        db.execute(delete(Clip).where(Clip.event_id.in_(event_ids)))
        db.execute(delete(Event).where(Event.camera_id == camera_id))
        db.execute(delete(AlertRule).where(AlertRule.zone_id.in_(zone_ids)))
        db.execute(delete(Zone).where(Zone.camera_id == camera_id))
        db.execute(delete(Camera).where(Camera.id == camera_id))
        db.commit()


if __name__ == "__main__":
    sys.exit(main())
