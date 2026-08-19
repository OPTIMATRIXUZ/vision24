import logging
import time as time_mod
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.models import Camera, Embedding, Event, Site, Zone
from app.services import analytics
from app.services.ai.provider import ImagePart, ToolSpec

log = logging.getLogger(__name__)

MAX_CLIPS_TO_SHOW = 3
VERIFY_MAX_CLIPS = 2
VERIFY_FRAMES_PER_CLIP = 3
EVENTS_IN_PAYLOAD = 50

EVENT_TYPES = ["entry", "exit", "occupancy", "queue_len", "dwell"]

TOOL_SPECS = [
    ToolSpec(
        name="find_events",
        description=(
            "Search CCTV detection events. Use for counts, who/when entered or left, "
            "peaks, queues, dwell times. event_type: 'entry' = a person entering a zone "
            "(the count over a period is the TOTAL VISITS to that zone), 'queue_len' for "
            "queues, 'occupancy' for how many people were present simultaneously, "
            "'dwell' for how long people stayed, 'exit' for leaving."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "enum": EVENT_TYPES},
                "zone_name": {
                    "type": "string",
                    "description": "Exact zone name from the zone list. Omit for the whole store.",
                },
                "time_from": {
                    "type": "string",
                    "description": "Site-local ISO 8601 without offset, e.g. 2026-07-13T00:00:00",
                },
                "time_to": {"type": "string", "description": "Site-local ISO 8601 without offset"},
            },
            "required": ["event_type", "time_from", "time_to"],
        },
    ),
    ToolSpec(
        name="get_summary",
        description=(
            "Daily summary: total entries, peak occupancy with time, average dwell per zone."
        ),
        parameters={
            "type": "object",
            "properties": {"day": {"type": "string", "description": "YYYY-MM-DD. Omit for today."}},
        },
    ),
    ToolSpec(
        name="get_traffic",
        description="Hourly visitor traffic (entry counts per hour) for a day.",
        parameters={
            "type": "object",
            "properties": {"day": {"type": "string", "description": "YYYY-MM-DD. Omit for today."}},
        },
    ),
    ToolSpec(
        name="get_live_metrics",
        description=(
            "Current state: people in store now, per-zone occupancy, queue lengths vs thresholds."
        ),
    ),
    ToolSpec(
        name="list_zones",
        description="List configured zones (name and kind).",
    ),
    ToolSpec(
        name="get_alerts",
        description="Recent triggered alerts (queue/occupancy threshold breaches).",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max alerts to return (default 10, max 50)",
                }
            },
        },
    ),
    ToolSpec(
        name="search_frames",
        description=(
            "Semantic search over analyzed CCTV snapshot frames by VISUAL description "
            "(clothing, objects, appearance) — e.g. 'person in a red jacket'. "
            "query_en MUST be in English — translate the user's request first. "
            "Matched frames are shown to the user automatically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query_en": {"type": "string", "description": "English visual description"},
                "top_k": {"type": "integer", "description": "1-8, default 5"},
            },
            "required": ["query_en"],
        },
    ),
    ToolSpec(
        name="get_clips",
        description=(
            "Attach video clips for specific event IDs to your answer — they render as "
            "playable videos in the user's chat. You cannot watch them yourself. ONLY call "
            "this when the user asks to SEE or SHOW footage — never for numeric questions. "
            "Use event IDs returned by find_events. Max 3 clips."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_ids": {"type": "array", "items": {"type": "integer"}},
                "reason": {"type": "string", "description": "Why footage is needed"},
            },
            "required": ["event_ids"],
        },
    ),
    ToolSpec(
        name="get_live_view",
        description=(
            "LOOK at the camera right now — the frames come back to YOU as images, so you "
            "genuinely see them. Call this whenever the user asks what is happening, what "
            "they or someone else is doing, or what you can see: «что я делаю», «что "
            'происходит», «что видишь», «опиши обстановку», "what am I doing", "what do '
            'you see". Then describe only what is actually visible in the frames. Pair with '
            "get_live_metrics when a count is asked for."
        ),
        parameters={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": (
                        "How many frames to grab, 1-4 (default 2 — two frames show motion)"
                    ),
                }
            },
        },
    ),
    ToolSpec(
        name="get_live_frame",
        description=(
            "DISPLAY recent stored entry snapshots to the user as thumbnails. Use only when "
            "they ask to be shown past captures — «покажи кадр», «покажи кто заходил». These "
            "images render in the user's chat and you do NOT see them, so never describe "
            "their content. To see the scene yourself, call get_live_view instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "How many recent frames (1-6, default 3)",
                }
            },
        },
    ),
    ToolSpec(
        name="get_deliveries",
        description=(
            "Delivery counting results for a day: how many carrying trips the workers made "
            "from the truck into the shop and how many packages of each product were brought "
            "in (with derived retail units where configured). A delivery TRIP is one walk "
            "truck→door, not one visitor — do not mix these numbers with entry counts. "
            "Evidence snapshots of each trip are shown to the user automatically; their "
            "event IDs work with get_clips/verify_footage."
        ),
        parameters={
            "type": "object",
            "properties": {
                "day": {"type": "string", "description": "YYYY-MM-DD. Omit for today."},
                "product_name": {
                    "type": "string",
                    "description": "Filter the totals to one product (exact name).",
                },
            },
        },
    ),
    ToolSpec(
        name="get_pos_discrepancies",
        description=(
            "POS ↔ camera reconciliation for a day: suspicious cash-register operations "
            "found by comparing the receipt feed with what the camera saw at the checkout. "
            "Flags: 'no_person_at_sale' (a sale registered at an empty checkout), "
            "'void_no_customer' (a void with nobody present), 'unscanned_visit' (a customer "
            "stood at the checkout but no sale was registered). The receipt feed is currently "
            "a SIMULATED Flowpos integration (demo data in the real Flowpos API format) — "
            "say so if asked about the data source. Evidence frames are shown to the user "
            "automatically."
        ),
        parameters={
            "type": "object",
            "properties": {"day": {"type": "string", "description": "YYYY-MM-DD. Omit for today."}},
        },
    ),
    ToolSpec(
        name="get_savings",
        description=(
            "The saved-money estimate for a month: prevented losses in UZS, broken into "
            "transparent count × constant lines (queue walk-aways prevented, after-hours "
            "entries caught, unmatched delivery packages, flagged POS operations), compared "
            "against the subscription price. Use for «сколько мы сэкономили», ROI and "
            "value-of-the-product questions. Present the arithmetic per line — the formula "
            "being inspectable is the point."
        ),
        parameters={
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "YYYY-MM. Omit for the current month."}
            },
        },
    ),
    ToolSpec(
        name="verify_footage",
        description=(
            "LOOK AT the footage for specific event IDs and describe/verify what is visible "
            "(people, actions, objects, queues). A few sampled frames of each clip are sent to "
            "you as images — base your description only on what you genuinely see. Use ONLY when "
            "the user asks what is happening in the footage or to confirm something visual — never "
            f"for numeric questions. Uses event IDs from find_events. Max {VERIFY_MAX_CLIPS} clips."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_ids": {"type": "array", "items": {"type": "integer"}},
                "question": {
                    "type": "string",
                    "description": "What to look for / verify in the footage",
                },
            },
            "required": ["event_ids"],
        },
    ),
]


@dataclass
class ToolContext:
    db: Session
    site: Site
    zones: list[Zone]
    tz: ZoneInfo
    events_out: dict[int, dict] = field(default_factory=dict)
    clips_out: list[dict] = field(default_factory=list)
    images_pending: list[ImagePart] = field(default_factory=list)


def dispatch(name: str, args: dict, ctx: ToolContext) -> dict:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return handler(args, ctx)
    except Exception as exc:
        log.exception("Tool %s failed with args %s", name, args)
        return {"error": f"{type(exc).__name__} — see the server log for details"}


def _local(ts: datetime, tz: ZoneInfo) -> str:
    return ts.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_local(value: str, tz: ZoneInfo) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts.replace(tzinfo=tz) if ts.tzinfo is None else ts


def _parse_day(args: dict) -> date | None:
    return date.fromisoformat(args["day"]) if args.get("day") else None


def _find_events(args: dict, ctx: ToolContext) -> dict:
    zone_name = args.get("zone_name")
    zone = analytics.resolve_zone(ctx.zones, zone_name)
    if zone_name and zone is None:
        return {
            "error": f"Unknown zone {zone_name!r}",
            "known_zones": [z.name for z in ctx.zones],
        }
    ts_from = _parse_local(args["time_from"], ctx.tz)
    ts_to = _parse_local(args["time_to"], ctx.tz)
    events = analytics.find_events(ctx.db, ctx.site, args["event_type"], zone, ts_from, ts_to)

    zone_names = {z.id: z.name for z in ctx.zones}
    shown = events[:EVENTS_IN_PAYLOAD]
    snapshots = analytics.snapshot_keys_for_events(ctx.db, ctx.site, [e.id for e in shown])
    for e in shown:
        ctx.events_out.setdefault(
            e.id,
            {
                "id": e.id,
                "type": e.type,
                "zone_name": zone_names.get(e.zone_id),
                "ts_start": e.ts_start,
                "attributes": e.attributes or {},
                "snapshot_url": storage.presign_get(snapshots[e.id]) if e.id in snapshots else None,
            },
        )
    return {
        "count": len(events),
        "truncated": len(events) >= analytics.MAX_EVENTS,
        "summary": analytics.summarize_events(args["event_type"], events, zone_names, ctx.tz),
        "events": [
            {
                "id": e.id,
                "zone": zone_names.get(e.zone_id),
                "ts": _local(e.ts_start, ctx.tz),
                "attributes": e.attributes or {},
            }
            for e in events[:EVENTS_IN_PAYLOAD]
        ],
    }


def _get_summary(args: dict, ctx: ToolContext) -> dict:
    summary = analytics.get_summary(ctx.db, ctx.site, _parse_day(args))
    out = summary.model_dump(mode="json")
    if summary.peak_occupancy.ts is not None:
        out["peak_occupancy"]["ts"] = _local(summary.peak_occupancy.ts, ctx.tz)
    return out


def _get_traffic(args: dict, ctx: ToolContext) -> dict:
    buckets = analytics.get_traffic(ctx.db, ctx.site, _parse_day(args))
    return {
        "buckets": [
            {"hour": b.bucket_start.strftime("%H:%M"), "entries": b.entries} for b in buckets
        ]
    }


def _get_live_metrics(args: dict, ctx: ToolContext) -> dict:
    return analytics.get_live_metrics(ctx.db, ctx.site).model_dump(mode="json")


def _list_zones(args: dict, ctx: ToolContext) -> dict:
    return {"zones": [{"name": z.name, "kind": z.kind} for z in ctx.zones]}


def _get_alerts(args: dict, ctx: ToolContext) -> dict:
    limit = min(int(args.get("limit", 10)), 50)
    alerts = analytics.get_alerts(ctx.db, ctx.site, limit)
    return {
        "alerts": [
            {
                "time": _local(a.triggered_at, ctx.tz),
                "message": a.message,
                "value": a.value,
                "status": a.status,
            }
            for a in alerts
        ]
    }


def _search_frames(args: dict, ctx: ToolContext) -> dict:
    from app.services.embeddings import get_embedder

    query_vec = get_embedder().embed_text(args["query_en"])
    top_k = max(1, min(int(args.get("top_k", 5)), 8))
    dist = Embedding.vec.cosine_distance(query_vec.tolist())
    rows = ctx.db.execute(
        select(Event, dist.label("dist"))
        .join(Embedding, Embedding.event_id == Event.id)
        .join(Camera, Event.camera_id == Camera.id)
        .where(Camera.site_id == ctx.site.id)
        .order_by(dist)
        .limit(top_k)
    ).all()
    if not rows:
        return {
            "matches": [],
            "note": (
                "No frame embeddings yet — run an analysis first "
                "(semantic index is built per analysis)."
            ),
        }

    zone_names = {z.id: z.name for z in ctx.zones}
    snapshots = analytics.snapshot_keys_for_events(ctx.db, ctx.site, [r.Event.id for r in rows])
    matches = []
    for r in rows:
        e = r.Event
        ctx.events_out.setdefault(
            e.id,
            {
                "id": e.id,
                "type": e.type,
                "zone_name": zone_names.get(e.zone_id),
                "ts_start": e.ts_start,
                "attributes": e.attributes or {},
                "snapshot_url": storage.presign_get(snapshots[e.id]) if e.id in snapshots else None,
            },
        )
        matches.append(
            {
                "event_id": e.id,
                "ts": _local(e.ts_start, ctx.tz),
                "zone": zone_names.get(e.zone_id),
                "similarity": round(1.0 - float(r.dist), 3),
            }
        )
    return {"matches": matches, "note": "Matched frames are shown to the user below your answer."}


def _get_clips(args: dict, ctx: ToolContext) -> dict:
    event_ids = [int(i) for i in args.get("event_ids", [])][:20]
    clips = analytics.get_clips_for_events(ctx.db, ctx.site, event_ids, MAX_CLIPS_TO_SHOW)
    meta = []
    for clip in clips:
        ctx.clips_out.append(
            {
                "event_id": clip.event_id,
                "url": storage.presign_get(clip.storage_key),
                "ts_start": clip.ts_start,
            }
        )
        meta.append(
            {
                "event_id": clip.event_id,
                "ts": _local(clip.ts_start, ctx.tz),
                "duration_s": clip.duration_s,
            }
        )
    if not meta:
        return {"clips": [], "note": "No stored clips found for these events."}
    return {
        "clips": meta,
        "note": (
            f"{len(meta)} clip(s) are now shown to the user below your answer. "
            "Reference them; you cannot see their content."
        ),
    }


LIVE_VIEW_MAX_FRAMES = 4
LIVE_VIEW_GAP_S = 0.6


def _get_live_view(args: dict, ctx: ToolContext) -> dict:
    from app.config import settings
    from app.services import capture as capture_service
    from app.services.frames import prepare_vlm_jpeg

    camera = ctx.db.scalars(
        select(Camera)
        .where(Camera.site_id == ctx.site.id, Camera.role == "cctv", Camera.is_active)
        .order_by(Camera.name)
        .limit(1)
    ).first()
    if camera is not None:
        source, label = camera.rtsp_url, camera.name
        zones = [z for z in ctx.zones if z.camera_id == camera.id]
    else:
        source, label = settings.replay_target, "live"
        zones = ctx.zones

    n = max(1, min(int(args.get("count", 2)), LIVE_VIEW_MAX_FRAMES))
    frames: list[bytes] = []
    for i in range(n):
        if i:
            time_mod.sleep(LIVE_VIEW_GAP_S)
        try:
            raw = capture_service.live_frame(source)
        except capture_service.CaptureError as exc:
            log.warning("get_live_view: grab failed: %s", exc)
            break
        jpg = prepare_vlm_jpeg(raw, zones)
        if jpg:
            frames.append(jpg)

    if not frames:
        return {
            "frames": 0,
            "note": (
                "The live feed is not playing right now, so there is nothing to look at. "
                "Tell the user to start it with the «Смотреть» button."
            ),
        }
    for jpg in frames:
        ctx.images_pending.append(ImagePart(data=jpg, mime_type="image/jpeg"))
    return {
        "frames": len(frames),
        "camera": label,
        "note": (
            f"{len(frames)} live frame(s) from «{label}» are attached — "
            "describe what you actually see in them."
        ),
    }


def _get_live_frame(args: dict, ctx: ToolContext) -> dict:
    n = max(1, min(int(args.get("count", 3)), 6))
    rows = (
        ctx.db.execute(
            select(Event)
            .join(Camera, Event.camera_id == Camera.id)
            .where(Camera.site_id == ctx.site.id, Event.type == "entry")
            .order_by(Event.ts_start.desc())
            .limit(n * 4)
        )
        .scalars()
        .all()
    )
    snaps = analytics.snapshot_keys_for_events(ctx.db, ctx.site, [e.id for e in rows])
    zone_names = {z.id: z.name for z in ctx.zones}
    shown = 0
    for e in rows:
        if shown >= n:
            break
        if e.id not in snaps:
            continue
        ctx.events_out.setdefault(
            e.id,
            {
                "id": e.id,
                "type": e.type,
                "zone_name": zone_names.get(e.zone_id),
                "ts_start": e.ts_start,
                "attributes": e.attributes or {},
                "snapshot_url": storage.presign_get(snaps[e.id]),
            },
        )
        shown += 1
    if shown == 0:
        return {
            "frames": 0,
            "note": "No stored frames yet — the detector hasn't captured a snapshot.",
        }
    return {
        "frames": shown,
        "note": f"{shown} recent annotated frame(s) are shown to the user below your answer.",
    }


def _verify_footage(args: dict, ctx: ToolContext) -> dict:
    from app.services.frames import mask_jpegs, sample_jpeg_frames

    event_ids = [int(i) for i in args.get("event_ids", [])][:20]
    clips = analytics.get_clips_for_events(ctx.db, ctx.site, event_ids, VERIFY_MAX_CLIPS)
    event_by_id = (
        {
            row.id: row
            for row in ctx.db.execute(
                select(Event.id, Event.camera_id, Event.ts_start).where(
                    Event.id.in_([c.event_id for c in clips])
                )
            )
        }
        if clips
        else {}
    )
    attached = []
    total_frames = 0
    for clip in clips:
        event = event_by_id.get(clip.event_id)
        anchor_frac = None
        if event is not None and clip.duration_s:
            anchor_frac = (event.ts_start - clip.ts_start).total_seconds() / clip.duration_s
            anchor_frac = max(0.0, min(1.0, anchor_frac))
        try:
            data = storage.download_bytes(clip.storage_key)
            frames = sample_jpeg_frames(
                data,
                VERIFY_FRAMES_PER_CLIP,
                people_frames=clip.people_frames,
                anchor_frac=anchor_frac,
            )
            frames = mask_jpegs(
                frames,
                [z for z in ctx.zones if event is not None and z.camera_id == event.camera_id],
            )
        except Exception as exc:  # noqa: BLE001 — see below
            log.warning("verify_footage: could not load clip %s: %s", clip.storage_key, exc)
            continue
        if not frames:
            continue
        for jpg in frames:
            ctx.images_pending.append(ImagePart(data=jpg, mime_type="image/jpeg"))
        total_frames += len(frames)
        ctx.clips_out.append(
            {
                "event_id": clip.event_id,
                "url": storage.presign_get(clip.storage_key),
                "ts_start": clip.ts_start,
            }
        )
        attached.append(
            {
                "event_id": clip.event_id,
                "ts": _local(clip.ts_start, ctx.tz),
                "frames": len(frames),
            }
        )
    if not attached:
        return {
            "clips": [],
            "note": "No stored clips for these events — cannot verify visually. "
            "Only zones with clip recording (or alert-triggering events) have clips.",
        }
    return {
        "clips": attached,
        "note": (
            f"{total_frames} frame(s) sampled from {len(attached)} clip(s) are attached below — "
            "describe what is actually visible in them. The clips also appear in the user's chat."
        ),
    }


def _get_deliveries(args: dict, ctx: ToolContext) -> dict:
    summary = analytics.get_delivery_summary(ctx.db, ctx.site, _parse_day(args))
    product_name = (args.get("product_name") or "").strip().lower()
    totals = summary.totals
    if product_name:
        totals = [t for t in totals if t.product_name.lower() == product_name]

    for trip in summary.trips:
        ctx.events_out.setdefault(
            trip.event_id,
            {
                "id": trip.event_id,
                "type": "delivery_trip",
                "zone_name": trip.zone_name,
                "ts_start": trip.ts_start,
                "attributes": {
                    "items": [i.model_dump() for i in trip.items],
                    "unmatched": trip.unmatched,
                },
                "snapshot_url": trip.snapshot_url,
            },
        )
    return {
        "day": summary.day,
        "trips": len(summary.trips),
        "unmatched_packages": summary.unmatched_packages,
        "totals": [
            {
                "product": t.product_name,
                "packages": t.packages,
                "units": t.units,
                "unit_label": t.unit_label,
            }
            for t in totals
        ],
        "trip_details": [
            {
                "event_id": t.event_id,
                "from": _local(t.ts_start, ctx.tz),
                "to": _local(t.ts_end, ctx.tz) if t.ts_end else None,
                "items": [
                    {"product": i.product_name, "count": i.count, "confidence": i.confidence}
                    for i in t.items
                ],
                "unmatched": t.unmatched,
            }
            for t in summary.trips
        ],
    }


def _get_pos_discrepancies(args: dict, ctx: ToolContext) -> dict:
    from app.services import pos

    out = pos.get_discrepancies(ctx.db, ctx.site, _parse_day(args))

    evidence_ids = [d.evidence_event_id for d in out.discrepancies if d.evidence_event_id]
    if evidence_ids:
        zone_names = {z.id: z.name for z in ctx.zones}
        rows = ctx.db.execute(
            select(Event)
            .join(Camera, Event.camera_id == Camera.id)
            .where(Event.id.in_(evidence_ids), Camera.site_id == ctx.site.id)
        ).scalars()
        urls = {d.evidence_event_id: d.snapshot_url for d in out.discrepancies}
        for e in rows:
            ctx.events_out.setdefault(
                e.id,
                {
                    "id": e.id,
                    "type": e.type,
                    "zone_name": zone_names.get(e.zone_id),
                    "ts_start": e.ts_start,
                    "attributes": e.attributes or {},
                    "snapshot_url": urls.get(e.id),
                },
            )

    return {
        "day": out.day,
        "receipts_total": out.receipts_total,
        "discrepancies": [
            {
                "flag": d.flag,
                "ts": _local(d.ts, ctx.tz),
                "ts_end": _local(d.ts_end, ctx.tz) if d.ts_end else None,
                "zone": d.zone_name,
                "receipt": (
                    {
                        "external_id": d.receipt.external_id,
                        "kind": d.receipt.kind,
                        "total_uzs": d.receipt.total,
                        "items": len(d.receipt.items),
                        "source": d.receipt.source,
                    }
                    if d.receipt
                    else None
                ),
                "explanation": d.explanation,
            }
            for d in out.discrepancies
        ],
        "note": (
            "Evidence frames are shown to the user below your answer. The receipt feed is a "
            "simulated Flowpos integration (demo data, real API format)."
        ),
    }


def _get_savings(args: dict, ctx: ToolContext) -> dict:
    from app.services import pos

    out = pos.get_savings(ctx.db, ctx.site, args.get("month") or None)
    return {
        "month": out.month,
        "currency": "UZS",
        "lines": [line.model_dump() for line in out.lines],
        "total": out.total,
        "subscription": out.subscription,
        "net": out.net,
        "constants": out.constants,
        "note": (
            "Each line is count × unit_value (UZS), except 'pos' where flags with a real "
            "receipt total count at that total. net = total − subscription."
        ),
    }


_HANDLERS = {
    "find_events": _find_events,
    "get_summary": _get_summary,
    "get_traffic": _get_traffic,
    "get_live_metrics": _get_live_metrics,
    "list_zones": _list_zones,
    "get_alerts": _get_alerts,
    "search_frames": _search_frames,
    "get_clips": _get_clips,
    "get_live_view": _get_live_view,
    "get_live_frame": _get_live_frame,
    "get_deliveries": _get_deliveries,
    "get_pos_discrepancies": _get_pos_discrepancies,
    "get_savings": _get_savings,
    "verify_footage": _verify_footage,
}
