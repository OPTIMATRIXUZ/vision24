import logging

from fastapi import APIRouter
from fastapi.responses import EventSourceResponse

from app.deps import DbDep, PrincipalDep, SiteDep
from app.errors import AIProviderError
from app.schemas import ChatIn, ChatTurnOut
from app.services.ai import chat as chat_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _role(body: ChatIn) -> str:
    return "live" if body.surface == "live" else "default"


@router.post("/chat", response_model=ChatTurnOut)
def chat(body: ChatIn, db: DbDep, site: SiteDep):
    try:
        return chat_service.run_chat(body.session_id, body.message, db, site, _role(body))
    except Exception as exc:
        log.exception("Chat turn failed")
        raise AIProviderError() from exc


@router.post("/chat/stream", response_class=EventSourceResponse)
def chat_stream(body: ChatIn, db: DbDep, site: SiteDep):
    try:
        yield from chat_service.run_chat_stream(
            body.session_id, body.message, db, site, _role(body)
        )
    except Exception:
        log.exception("Chat stream failed")
        yield {"type": "error", "message": AIProviderError.default_message}


@router.delete("/chat/{session_id}")
def delete_chat(session_id: str, principal: PrincipalDep):
    chat_service.delete_session(principal.tenant_id, session_id)
    return {"deleted": session_id}
