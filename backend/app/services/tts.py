import io
import logging
import threading
import wave

import numpy as np

from app.config import settings
from app.errors import TTSUnavailableError

log = logging.getLogger(__name__)

STREAMING_INTERVAL_S = 0.5

_model = None
_load_lock = threading.Lock()
_gen_lock = threading.Lock()


TTSError = TTSUnavailableError


def is_enabled() -> bool:
    return settings.tts_enabled


def _load():
    global _model
    if _model is not None:
        return _model
    with _load_lock:
        if _model is None:
            try:
                from mlx_audio.tts.utils import load_model
            except ImportError as exc:
                log.warning("mlx-audio is not importable: %s", exc)
                raise TTSError("Speech synthesis is not available on this deployment.") from exc
            log.info("TTS: loading %s", settings.tts_model)
            _model = load_model(settings.tts_model)
            log.info("TTS: model ready")
    return _model


def warmup() -> None:
    if not (settings.tts_enabled and settings.tts_warmup):
        return

    def _run():
        try:
            _load()
        except Exception:
            log.exception("TTS warmup failed — speech will be unavailable")

    threading.Thread(target=_run, name="tts-warmup", daemon=True).start()


def synthesize(text: str) -> bytes:
    text = (text or "").strip()
    if not text:
        raise TTSError("Nothing to speak")
    text = text[: settings.tts_max_chars]

    model = _load()
    kwargs = {
        "text": text,
        "lang_code": "auto",
        "stream": True,
        "streaming_interval": STREAMING_INTERVAL_S,
        "verbose": False,
    }
    if settings.tts_voice:
        kwargs["voice"] = settings.tts_voice

    chunks: list[np.ndarray] = []
    sample_rate = 24000
    with _gen_lock:
        for result in model.generate(**kwargs):
            chunks.append(np.asarray(result.audio).reshape(-1))
            sample_rate = result.sample_rate
    if not chunks:
        raise TTSError("Model produced no audio")
    return _to_wav(np.concatenate(chunks), sample_rate)


def _to_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
