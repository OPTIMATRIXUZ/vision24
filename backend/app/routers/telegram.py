from fastapi import APIRouter, Depends

from app.config import settings
from app.deps import DbDep, LocaleDep, SiteDep, require_admin
from app.errors import UpstreamError, ValidationError
from app.schemas import DigestIn, TelegramSettingsIn, TelegramSettingsOut
from app.services import telegram

router = APIRouter(tags=["telegram"])


def _out(site) -> TelegramSettingsOut:
    return TelegramSettingsOut(
        chat_id=site.telegram_chat_id,
        enabled=site.telegram_enabled,
        digest_time=site.telegram_digest_time,
        bot_configured=bool(settings.telegram_bot_token),
    )


@router.get("/telegram", response_model=TelegramSettingsOut)
def get_settings(db: DbDep, site: SiteDep):
    return _out(site)


@router.put("/telegram", response_model=TelegramSettingsOut, dependencies=[Depends(require_admin)])
def update_settings(body: TelegramSettingsIn, db: DbDep, site: SiteDep):
    site.telegram_chat_id = (body.chat_id or "").strip() or None
    site.telegram_enabled = body.enabled
    site.telegram_digest_time = body.digest_time
    db.add(site)
    db.commit()
    return _out(site)


@router.post("/telegram/test", dependencies=[Depends(require_admin)])
def send_test(db: DbDep, site: SiteDep):
    config = telegram._config_for_site(site.id)
    if config is None:
        raise ValidationError(
            "Telegram is not configured: set TELEGRAM_BOT_TOKEN in the environment "
            "and a chat id for this site."
        )
    token, chat_id = config
    out = telegram.send_message_sync(token, chat_id, f"Vision24: test message ({site.name})")
    if not out.get("ok"):
        raise UpstreamError(f"Telegram refused the message: {out.get('description')}")
    return {"sent": True}


@router.post("/telegram/digest", dependencies=[Depends(require_admin)])
def send_digest_now(body: DigestIn, db: DbDep, site: SiteDep, locale: LocaleDep):
    out = telegram.send_digest(db, site, body.day, locale)
    if not out.get("ok"):
        detail = out.get("description") or "not configured"
        if "not configured" in str(detail):
            raise ValidationError(str(detail))
        raise UpstreamError(f"Telegram refused the digest: {detail}")
    return {"sent": True}
