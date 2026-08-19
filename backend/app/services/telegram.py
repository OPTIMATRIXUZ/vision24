import logging
import threading
import time as time_mod
import uuid
from datetime import date

import requests

from app.config import settings
from app.db import SessionLocal
from app.i18n import DEFAULT_LOCALE

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
SNAPSHOT_POLL_S = 3.0
SNAPSHOT_WAIT_S = 240.0

MUTED = False


def _config_for_site(site_id) -> tuple[str, str] | None:
    if not settings.telegram_bot_token:
        return None
    from app.models import Site

    with SessionLocal() as db:
        site = db.get(Site, site_id)
    if site is None or not getattr(site, "telegram_enabled", True):
        return None
    chat_id = (site.telegram_chat_id or "").strip() or settings.telegram_chat_id
    if not chat_id:
        return None
    return settings.telegram_bot_token, chat_id


def _site_id_for_camera(camera_id) -> uuid.UUID | None:
    from sqlalchemy import select

    from app.models import Camera

    with SessionLocal() as db:
        return db.scalar(select(Camera.site_id).where(Camera.id == camera_id))


def _wait_for_snapshot(event_id: int) -> bytes | None:
    from sqlalchemy import select

    from app import storage
    from app.models import Clip

    deadline = time_mod.monotonic() + SNAPSHOT_WAIT_S
    while time_mod.monotonic() < deadline:
        with SessionLocal() as db:
            key = db.scalar(
                select(Clip.snapshot_key).where(
                    Clip.event_id == event_id, Clip.snapshot_key.is_not(None)
                )
            )
        if key:
            try:
                return storage.download_bytes(key)
            except Exception:  # noqa: BLE001 — any storage failure degrades to text, never raises
                log.warning("Telegram: snapshot %s exists but could not be read", key)
                return None
        time_mod.sleep(SNAPSHOT_POLL_S)
    return None


def send_message_sync(token: str, chat_id: str, text: str) -> dict:
    res = requests.post(
        f"{API}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
    return res.json()


def _send_photo_sync(token: str, chat_id: str, photo: bytes, caption: str) -> dict:
    res = requests.post(
        f"{API}/bot{token}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("alert.jpg", photo, "image/jpeg")},
        timeout=30,
    )
    return res.json()


def send_alert(text: str, *, camera_id=None, event_id: int | None = None) -> None:
    if MUTED or not settings.telegram_bot_token:
        return

    def _send():
        try:
            config = None
            if camera_id is not None:
                site_id = _site_id_for_camera(camera_id)
                if site_id is not None:
                    config = _config_for_site(site_id)
            elif settings.telegram_chat_id:
                config = (settings.telegram_bot_token, settings.telegram_chat_id)
            if config is None:
                return
            token, chat_id = config

            photo = _wait_for_snapshot(event_id) if event_id is not None else None
            if photo is not None:
                out = _send_photo_sync(token, chat_id, photo, text)
                if out.get("ok"):
                    return
                log.warning("Telegram sendPhoto refused: %s", out.get("description"))
            out = send_message_sync(token, chat_id, text)
            if not out.get("ok"):
                log.warning("Telegram sendMessage refused: %s", out.get("description"))
        except Exception:
            log.warning("Telegram push failed", exc_info=True)

    threading.Thread(target=_send, daemon=True).start()


_DIGEST_LABELS = {
    "ru": {
        "title": "Vision24 — сводка за {date} ({site})",
        "entries": "Посетителей: {n}",
        "peak": "Пик: {n} чел. в {time}",
        "queues": "Очереди: {zone} до {n} (превышений: {breaches})",
        "alerts": "Алертов: {n}",
        "after_hours": "Входов после закрытия: {n}",
        "deliveries": "Поставки: {n} рейс(ов)",
        "pos": "Подозрительных кассовых операций: {n}",
        "savings": "Сэкономлено за {month}: {total} сум (чистыми {net})",
        "empty": "За этот день записей нет.",
    },
    "uz": {
        "title": "Vision24 — {date} kuni xulosasi ({site})",
        "entries": "Tashrif: {n}",
        "peak": "Eng gavjum: {n} kishi, {time}",
        "queues": "Navbat: {zone} — {n} gacha (buzilishlar: {breaches})",
        "alerts": "Ogohlantirishlar: {n}",
        "after_hours": "Yopilgandan keyingi kirishlar: {n}",
        "deliveries": "Yetkazib berish: {n} ta reys",
        "pos": "Shubhali kassa operatsiyalari: {n}",
        "savings": "{month} oyida tejaldi: {total} soʻm (sof {net})",
        "empty": "Bu kun uchun yozuvlar yoʻq.",
    },
    "en": {
        "title": "Vision24 — {date} summary ({site})",
        "entries": "Visitors: {n}",
        "peak": "Peak: {n} people at {time}",
        "queues": "Queues: {zone} up to {n} (breaches: {breaches})",
        "alerts": "Alerts: {n}",
        "after_hours": "After-hours entries: {n}",
        "deliveries": "Deliveries: {n} trips",
        "pos": "Suspicious POS operations: {n}",
        "savings": "Saved in {month}: {total} UZS (net {net})",
        "empty": "No records for this day.",
    },
}


def _fmt_money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def format_digest(context: dict, locale: str = DEFAULT_LOCALE) -> str:
    labels = _DIGEST_LABELS.get(locale, _DIGEST_LABELS["ru"])
    lines = [labels["title"].format(date=context["date"], site=context["site"])]

    if not context["entries_total"] and not context["alerts"]:
        lines.append(labels["empty"])
        return "\n".join(lines)

    lines.append(labels["entries"].format(n=context["entries_total"]))
    peak = context["peak_occupancy"]
    if peak["value"]:
        lines.append(labels["peak"].format(n=peak["value"], time=peak["time"]))
    for q in context["queues"]:
        lines.append(
            labels["queues"].format(
                zone=q["zone"], n=q["max_queue_len"], breaches=len(q["breaches"])
            )
        )
    if context["alerts"]:
        lines.append(labels["alerts"].format(n=len(context["alerts"])))
    if context["after_hours_entries"]:
        lines.append(labels["after_hours"].format(n=len(context["after_hours_entries"])))
    if context["deliveries"]["trips"]:
        lines.append(labels["deliveries"].format(n=context["deliveries"]["trips"]))
    pos = context.get("pos") or {}
    if pos.get("discrepancies"):
        lines.append(labels["pos"].format(n=len(pos["discrepancies"])))
    savings = context.get("savings") or {}
    if savings.get("total"):
        lines.append(
            labels["savings"].format(
                month=savings["month"],
                total=_fmt_money(savings["total"]),
                net=_fmt_money(savings["net"]),
            )
        )
    return "\n".join(lines)


def send_digest(db, site, day: date | None = None, locale: str = DEFAULT_LOCALE) -> dict:
    from app.services import analytics

    config = _config_for_site(site.id)
    if config is None:
        return {"ok": False, "description": "Telegram is not configured for this site."}
    token, chat_id = config
    context = analytics.build_report_context(db, site, day)
    return send_message_sync(token, chat_id, format_digest(context, locale))
