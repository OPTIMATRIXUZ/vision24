import logging
import threading
import time as time_mod
import uuid
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import storage
from app.errors import StorageError
from app.i18n import DEFAULT_LOCALE, LANGUAGE_NAMES
from app.models import Event, Site
from app.services import analytics
from app.services.ai.provider import ImagePart, Msg, TextPart, get_provider, is_configured

log = logging.getLogger(__name__)

MIN_LLM_GAP_S = 15.0
MIN_IMAGE_GAP_S = 60.0

SYSTEM_TEMPLATE = (
    "You are the live commentator of a store's CCTV analytics. You receive a compact "
    "digest of detection events from the last poll window — you never see video. "
    "Write 1-2 short sentences in {language} summarizing what is happening right now "
    "(visitors, queues, alerts). All numbers must come from the digest; never invent. "
    "If a snapshot image is attached, you may add what is visible in it. No greetings, "
    "no preamble — just the commentary line(s)."
)

DIGEST_HEADING = {
    "ru": "Данные за последний интервал:",
    "uz": "Soʻnggi oraliq maʼlumotlari:",
    "en": "Data from the last interval:",
}


def system_prompt(locale: str) -> str:
    return SYSTEM_TEMPLATE.format(language=LANGUAGE_NAMES.get(locale, LANGUAGE_NAMES["ru"]))


def digest_heading(locale: str) -> str:
    return DIGEST_HEADING.get(locale, DIGEST_HEADING["ru"])


_lock = threading.Lock()
_last_llm: dict[uuid.UUID, float] = {}
_last_image: dict[uuid.UUID, float] = {}


def reset_debounce() -> None:
    with _lock:
        _last_llm.clear()
        _last_image.clear()


def _digest(events: list[Event], zone_names: dict, tz) -> str:
    lines: list[str] = []
    entries = [e for e in events if e.type == "entry"]
    exits = [e for e in events if e.type == "exit"]
    if entries:
        by_zone = Counter(zone_names.get(e.zone_id, "store") for e in entries)
        detail = ", ".join(f"{z}: {n}" for z, n in by_zone.most_common())
        lines.append(f"- Entries: {len(entries)} ({detail})")
    if exits:
        lines.append(f"- Exits: {len(exits)}")
    occ = [e for e in events if e.type == "occupancy" and e.zone_id is None]
    if occ:
        latest = occ[-1]
        lines.append(f"- People in view now: {latest.attributes.get('count', 0)}")
    queues = [e for e in events if e.type == "queue_len"]
    if queues:
        peak = max(queues, key=lambda e: e.attributes.get("queue_len", 0))
        lines.append(
            f"- Queue at {zone_names.get(peak.zone_id, '?')}: "
            f"up to {peak.attributes.get('queue_len', 0)} people"
        )
    dwells = [e for e in events if e.type == "dwell"]
    if dwells:
        avg = sum(e.attributes.get("dwell_s", 0) for e in dwells) / len(dwells)
        lines.append(f"- Visits ended: {len(dwells)}, avg dwell {avg:.0f}s")
    if events:
        first, last = events[0].ts_start, events[-1].ts_start
        lines.append(f"- Window: {first.astimezone(tz):%H:%M:%S}–{last.astimezone(tz):%H:%M:%S}")
    return "\n".join(lines)


def _window_alerts(db: Session, site: Site, since: datetime) -> list:
    return [a for a in analytics.get_alerts(db, site, limit=5) if a.triggered_at >= since]


def generate(db: Session, site: Site, events: list[Event], locale: str = DEFAULT_LOCALE) -> dict:
    if not events:
        return {"skipped": True, "reason": "no_events"}

    now = time_mod.monotonic()
    with _lock:
        if now - _last_llm.get(site.id, 0.0) < MIN_LLM_GAP_S:
            return {"skipped": True, "reason": "debounce"}
        _last_llm[site.id] = now

    tz = analytics.site_tz(site)
    zones = analytics.list_zones(db, site)
    zone_names = {z.id: z.name for z in zones}
    digest = _digest(events, zone_names, tz)

    since = min(e.ts_start for e in events) - timedelta(seconds=5)
    alerts = _window_alerts(db, site, since)
    if alerts:
        digest += "\n- ALERTS: " + "; ".join(a.message for a in alerts)

    image: ImagePart | None = None
    if alerts:
        with _lock:
            image_ok = now - _last_image.get(site.id, 0.0) >= MIN_IMAGE_GAP_S
            if image_ok:
                _last_image[site.id] = now
        if image_ok:
            event_ids = [a.event_id for a in alerts if a.event_id is not None]
            snaps = analytics.snapshot_keys_for_events(db, site, event_ids)
            for key in snaps.values():
                try:
                    image = ImagePart(data=storage.download_bytes(key))
                    break
                except StorageError as exc:
                    log.warning("commentary: snapshot fetch failed %s: %s", key, exc)

    if not is_configured():
        return {
            "text": digest_heading(locale) + "\n" + digest,
            "degraded": True,
            "events_count": len(events),
            "with_image": False,
        }

    log.info(
        "commentary REQ: %d events, alerts=%d, image=%s\n%s",
        len(events),
        len(alerts),
        image is not None,
        digest,
    )
    parts: list = [TextPart("Digest of the last window:\n" + digest)]
    if image is not None:
        parts.append(image)
    try:
        resp = get_provider().generate(
            system=system_prompt(locale), messages=[Msg("user", parts)], tools=None
        )
        text = resp.text.strip()
    except Exception:
        log.exception("commentary LLM call failed")
        text = ""
    log.info("commentary RESP: %r", text[:400])
    if not text:
        return {
            "text": digest_heading(locale) + "\n" + digest,
            "degraded": True,
            "events_count": len(events),
            "with_image": False,
        }
    return {
        "text": text,
        "degraded": False,
        "events_count": len(events),
        "with_image": image is not None,
    }
