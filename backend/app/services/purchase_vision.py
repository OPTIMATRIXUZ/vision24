import json
import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.services.frames import apply_privacy_masks, read_frames_at

log = logging.getLogger(__name__)

FRAME_FRACS = (0.05, 0.25, 0.5, 0.75, 0.95)
MAX_VIEW_W = 640
JPEG_QUALITY = 70
CROP_MARGIN = 0.35
KINDS = ("sale", "administrative", "unclear")

PROMPT = (
    "You are reviewing frames from an overhead CCTV camera above a minimarket "
    "checkout counter (Uzbekistan). The frames below cover ONE visit to the checkout.\n\n"
    "The counter is a permanent display: candy jars, snack racks, lollipop trays "
    "and chocolate bars always sit on it — those are NOT purchases. Money, phones, "
    "calculators, notebooks and pens are NOT products.\n\n"
    "Decide:\n"
    '- "kind": "sale" if a purchase happens (goods handed over or bagged, OR a '
    "customer pays cash even when the goods themselves are not visible); "
    '"administrative" ONLY when the visit is clearly not a purchase (staff '
    'activity, supplier delivering stock, paperwork/bookkeeping); "unclear" otherwise.\n'
    "- Precedence: if retail goods are visibly handed to a visitor, picked up by "
    'them, or bagged, the visit is a "sale" — even if someone else in the frame '
    "is doing paperwork at the same time.\n"
    "- Only when NO goods change hands: staff counting banknotes OUT of the drawer "
    "to the visitor while a ledger/notebook/invoice is being written is a supplier "
    'settlement — "administrative".\n'
    '- "items": retail products actually being bought, [] if none visible.\n\n'
    "Reply with ONLY this JSON:\n"
    '{"visits": [{"visit": 1, "kind": "sale"|"administrative"|"unclear", '
    '"items": [{"name": "<product>", "qty": <int>}], "confidence": <0..1>, '
    '"notes": "<one line>"}]}'
)

NAMING_PROMPT = (
    "You are reviewing frames from an overhead CCTV camera above a minimarket "
    "checkout counter (Uzbekistan). The frames below cover ONE visit to the checkout.\n\n"
    "The counter is a permanent display: candy jars, snack racks, lollipop trays "
    "and chocolate bars always sit on it — those are NOT purchases. Money, phones, "
    "calculators, notebooks and pens are NOT products.\n\n"
    "List the retail products actually being bought in this visit (handed over, "
    "picked up by the customer, bagged, or paid for). [] if none are visible.\n"
    "The shop's known products include: {catalog}. Use an EXACT name from this "
    "list ONLY when the item's packaging or appearance clearly matches it; when "
    'you cannot tell, describe what you actually see ("small packaged item") '
    "instead of guessing a catalog name.\n\n"
    "Reply with ONLY this JSON:\n"
    '{{"items": [{{"name": "<product>", "qty": <int>}}]}}'
)
MAX_CATALOG_NAMES = 40
REFERENCE_PHOTOS = 8


@dataclass
class VisitWindow:

    zone_id: object
    polygon: list
    start_idx: int
    end_idx: int


@dataclass
class VisitVerdict:
    kind: str
    items: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""


def _view_bbox(polygon: list, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    x1, x2 = min(xs) * frame_w, max(xs) * frame_w
    y1, y2 = min(ys) * frame_h, max(ys) * frame_h
    mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
    return (
        max(0, int(x1 - mx)),
        max(0, int(y1 - my)),
        min(frame_w, int(x2 + mx)),
        min(frame_h, int(y2 + my)),
    )


def _view_jpeg(frame: np.ndarray, polygon: list, zones) -> bytes | None:
    apply_privacy_masks(frame, zones)
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = _view_bbox(polygon, w, h)
    if x2 - x1 < 32 or y2 - y1 < 32:
        return None
    crop = frame[y1:y2, x1:x2]
    if crop.shape[1] > MAX_VIEW_W:
        crop = cv2.resize(crop, (MAX_VIEW_W, int(MAX_VIEW_W * crop.shape[0] / crop.shape[1])))
    ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return jpg.tobytes() if ok else None


def frame_indices(visit: VisitWindow) -> list[int]:
    span = max(0, visit.end_idx - visit.start_idx)
    return sorted({visit.start_idx + int(span * f) for f in FRAME_FRACS})


def _parse_verdict(text: str) -> VisitVerdict | None:
    try:
        raw = json.loads(text[text.find("{") : text.rfind("}") + 1])
        entry = raw["visits"][0]
        kind = str(entry.get("kind", "")).strip().lower()
        if kind not in KINDS:
            return None
        items = []
        for it in entry.get("items", []) or []:
            name = str(it.get("name", "")).strip()
            qty = int(it.get("qty", 1))
            if name and 1 <= qty <= 99:
                items.append({"name": name[:80], "qty": qty})
        confidence = min(1.0, max(0.0, float(entry.get("confidence", 0.0))))
        notes = str(entry.get("notes", ""))[:300]
        return VisitVerdict(kind=kind, items=items, confidence=confidence, notes=notes)
    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
        return None


def _parse_items(text: str) -> list[dict] | None:
    try:
        raw = json.loads(text[text.find("{") : text.rfind("}") + 1])
        items = []
        for it in raw.get("items", []) or []:
            name = str(it.get("name", "")).strip()
            qty = int(it.get("qty", 1))
            if name and 1 <= qty <= 99:
                items.append({"name": name[:80], "qty": qty})
        return items
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def naming_parts(catalog_names: list[str]) -> str:
    names = ", ".join(catalog_names[:MAX_CATALOG_NAMES])
    return NAMING_PROMPT.format(catalog=names)


def load_checkout_catalog(site_id) -> tuple[list[str], list[tuple[str, bytes]]]:
    from sqlalchemy import select

    from app import storage
    from app.db import SessionLocal
    from app.models import ProductSample, ProductType

    with SessionLocal() as db:
        products = db.scalars(
            select(ProductType)
            .where(ProductType.site_id == site_id)
            .order_by(ProductType.created_at)
        ).all()
        names = [p.name for p in products]
        references: list[tuple[str, bytes]] = []
        for p in sorted(products, key=lambda p: p.created_at, reverse=True):
            if len(references) >= REFERENCE_PHOTOS:
                break
            sample = db.scalars(
                select(ProductSample)
                .where(ProductSample.product_type_id == p.id)
                .order_by(ProductSample.created_at)
            ).first()
            if sample is None:
                continue
            try:
                references.append((p.name, storage.download_bytes(sample.storage_key)))
            except Exception:
                log.exception("Could not load product sample for %s", p.name)
    return names, references


def describe_visits(
    path: str,
    visits: list[VisitWindow],
    zones,
    catalog_names: list[str] | None = None,
    references: list[tuple[str, bytes]] | None = None,
) -> list[VisitVerdict | None]:
    from app.services.ai.provider import ImagePart, Msg, TextPart, get_provider

    if not visits:
        return []

    wanted: dict[int, list[int]] = {}
    for vi, visit in enumerate(visits):
        for idx in frame_indices(visit):
            wanted.setdefault(idx, []).append(vi)

    jpegs: dict[int, list[bytes]] = {vi: [] for vi in range(len(visits))}
    for idx, frame in read_frames_at(path, wanted):
        for vi in wanted[idx]:
            jpg = _view_jpeg(frame.copy(), visits[vi].polygon, zones)
            if jpg:
                jpegs[vi].append(jpg)

    provider = get_provider()
    verdicts: list[VisitVerdict | None] = []
    for vi in range(len(visits)):
        if len(jpegs[vi]) < 2:
            verdicts.append(None)
            continue
        parts: list = [TextPart(PROMPT), TextPart("Visit 1:")]
        parts.extend(ImagePart(data=jpg) for jpg in jpegs[vi])
        try:
            resp = provider.generate(
                system="You review CCTV frames. Answer with strict JSON only.",
                messages=[Msg(role="user", parts=parts)],
            )
            verdict = _parse_verdict(resp.text)
        except Exception:
            log.exception("VLM visit description failed (visit %d)", vi)
            verdicts.append(None)
            continue

        if verdict is not None and catalog_names and verdict.kind != "administrative":
            named = _name_items(provider, jpegs[vi], catalog_names, references or [], visit_no=vi)
            if named is not None:
                verdict.items = named
        verdicts.append(verdict)
    return verdicts


def _name_items(
    provider, frames: list[bytes], catalog_names: list[str], references, visit_no: int
) -> list[dict] | None:
    from app.services.ai.provider import ImagePart, Msg, TextPart

    parts: list = [TextPart(naming_parts(catalog_names)), TextPart("Visit 1:")]
    parts.extend(ImagePart(data=jpg) for jpg in frames)
    for name, jpeg in references[:REFERENCE_PHOTOS]:
        parts.append(TextPart(f"Reference photo of '{name}':"))
        parts.append(ImagePart(data=jpeg))
    try:
        resp = provider.generate(
            system="You review CCTV frames. Answer with strict JSON only.",
            messages=[Msg(role="user", parts=parts)],
        )
        return _parse_items(resp.text)
    except Exception:
        log.exception("VLM item naming failed (visit %d)", visit_no)
        return None
