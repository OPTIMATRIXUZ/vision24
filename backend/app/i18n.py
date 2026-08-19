import logging

LOCALES = ("ru", "uz", "en")
DEFAULT_LOCALE = "en"
LOCALE_COOKIE = "v24_locale"

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    "ru": "Russian",
    "uz": "Uzbek (Latin script)",
    "en": "English",
}


def normalize(value: str | None) -> str | None:
    if not value:
        return None
    tag = value.strip().lower().replace("_", "-").split("-")[0]
    return tag if tag in LOCALES else None


def from_accept_language(header: str | None) -> str | None:
    if not header:
        return None
    candidates: list[tuple[float, str]] = []
    for index, part in enumerate(header.split(",")):
        bits = part.split(";")
        tag = normalize(bits[0])
        if not tag:
            continue
        quality = 1.0
        for bit in bits[1:]:
            if bit.strip().startswith("q="):
                try:
                    quality = float(bit.strip()[2:])
                except ValueError:
                    quality = 0.0
        candidates.append((-quality, index, tag))  # type: ignore[arg-type]
    if not candidates:
        return None
    return sorted(candidates)[0][2]  # type: ignore[return-value]


def resolve_locale(cookie: str | None, accept_language: str | None = None) -> str:
    return normalize(cookie) or from_accept_language(accept_language) or DEFAULT_LOCALE
