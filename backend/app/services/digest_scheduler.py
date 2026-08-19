import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal

log = logging.getLogger(__name__)

TICK_S = 60.0


def _tick() -> None:
    from zoneinfo import ZoneInfo

    from app.models import Site
    from app.services import telegram

    with SessionLocal() as db:
        sites = db.scalars(
            select(Site).where(
                Site.telegram_enabled,
                Site.telegram_digest_time.is_not(None),
            )
        ).all()
        for site in sites:
            try:
                now_local = datetime.now(ZoneInfo(site.timezone))
                today = now_local.date()
                if site.telegram_digest_last_sent_on == today:
                    continue
                if now_local.time() < site.telegram_digest_time:
                    continue
                out = telegram.send_digest(db, site, today)
                if out.get("ok"):
                    site.telegram_digest_last_sent_on = today
                    db.add(site)
                    db.commit()
                    from datetime import timedelta

                    from app.services import aggregates

                    aggregates.compute_daily_stats_safe(db, site, today - timedelta(days=1))
                else:
                    log.warning("Digest for site %s not sent: %s", site.id, out.get("description"))
            except Exception:
                log.exception("Digest tick failed for site %s", site.id)


async def run_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(_tick)
        except Exception:
            log.exception("Digest scheduler tick crashed")
        await asyncio.sleep(TICK_S)
