import logging

from fastapi import APIRouter, Response

from app.deps import PrincipalDep
from app.errors import TTSUnavailableError, UnavailableError
from app.schemas import TTSIn
from app.services import tts as tts_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["tts"])


@router.get("/tts")
def tts_status(principal: PrincipalDep):
    return {"enabled": tts_service.is_enabled()}


@router.post("/tts")
def tts(body: TTSIn, principal: PrincipalDep):
    if not tts_service.is_enabled():
        raise TTSUnavailableError("Speech synthesis is disabled on this deployment.")
    try:
        audio = tts_service.synthesize(body.text)
    except TTSUnavailableError:
        raise
    except Exception as exc:
        log.exception("TTS synthesis failed")
        raise UnavailableError("Speech synthesis failed.") from exc
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "private, max-age=300"},
    )
