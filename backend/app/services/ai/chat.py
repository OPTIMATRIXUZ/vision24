import logging
import threading
import time as time_mod
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import storage
from app.config import settings
from app.models import Site, Zone
from app.schemas import ChatClipOut, ChatEventOut, ChatTurnOut, ToolCallTrace
from app.services import analytics
from app.services.ai import tools
from app.services.ai.provider import (
    ImagePart,
    LLMResponse,
    Msg,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    get_provider,
    is_configured,
)

log = logging.getLogger(__name__)

SESSION_TTL_S = 2 * 3600

SYSTEM_PROMPT = """You are the CCTV analytics assistant of a store manager.

LANGUAGE — the question is always in exactly one of three languages: Russian,
Uzbek (Latin or Cyrillic), or English. Detect it and write your ENTIRE answer in
that one language. Never mix languages in a single answer. Tool results are in
English and may contain Russian labels regardless of the question's language —
translate their content into the answer's language; never copy foreign-language
words verbatim. Keep answers concise (1-3 sentences unless asked for detail).

Site vocabulary:
- Zones (name — kind): {zones}
- Site timezone: {tz}. Current local datetime: {now}.
- Closing time: {closing}.

Rules:
- ALL numbers must come from tool results — never estimate or invent data.
- To describe WHAT IS HAPPENING or what someone is DOING right now («что я делаю»,
  «что происходит», «что видишь», «опиши обстановку», "what am I doing", "what do you
  see") call get_live_view — those frames come back to you as images and you really
  see them. Describe only what is visible; add get_live_metrics if a count matters.
  DON'T ask for clarification on such questions — just look.
- get_live_frame is different: it only DISPLAYS stored snapshots to the user
  («покажи кадр», «покажи кто заходил»). You cannot see those, so never describe them.
  (For "сколько / how many" just give the number — no frames needed.)
- "Сколько человек было в зоне X / за день" = the COUNT of entry events in that zone
  (find_events with event_type=entry and the zone_name) — each entry is one visit.
  occupancy events answer "how many at once / peak". When asked about a zone total,
  give the entries count; add the peak only if it clarifies. Re-entries by the same
  person count as separate visits (use the word for "visits" in the answer's own
  language when precision matters — never a different language than the rest of the answer).
- Map synonyms and languages to zone kinds: склад/ombor/storeroom/warehouse -> the
  store_room-kind zone; касса/kassa/checkout/queue -> the checkout_area-kind zone;
  вход/kirish/entrance -> the entrance-kind zone. Pass the exact zone NAME to tools.
- Time expressions (site-local), in any of the three languages:
  "до обеда" / "tushlikkacha" / "before lunch" = 00:00-13:00 today;
  "после обеда" / "tushlikdan keyin" / "after lunch" = 13:00 until now;
  "после закрытия" / "yopilgandan keyin" / "after closing" = closing time until
  06:00 the NEXT day;
  "сегодня" / "bugun" / "today" = today 00:00 until now;
  "вчера" / "kecha" / "yesterday" = the full previous day.
  No time mentioned = today 00:00-now.
- Never call get_clips unless the user asks to see or show footage.
  Answer numeric questions from tool data only.
- get_clips just DISPLAYS clips to the user below your answer (for them to watch);
  point the user to them in the answer's own language (Russian «смотрите клипы
  ниже», Uzbek «quyidagi kliplarni koʻring», English "see the clips below").
- To describe WHAT IS VISIBLE in footage (e.g. «что происходит на клипе», «проверь, кто зашёл»),
  call verify_footage with the event IDs — a few sampled frames are sent to you as images and you
  describe only what you genuinely see. Do not guess about visible content without calling it first.
- If a tool returns zero events, say so plainly based on the data.
"""


@dataclass
class _Session:
    messages: list[Msg] = field(default_factory=list)
    last_used: float = field(default_factory=time_mod.monotonic)


_sessions: dict[tuple[uuid.UUID, str], _Session] = {}
_lock = threading.Lock()


def _evict_locked(now: float, protect: tuple[uuid.UUID, str]) -> None:
    for key in [k for k, sess in _sessions.items() if now - sess.last_used > SESSION_TTL_S]:
        if key != protect:
            del _sessions[key]
    overflow = len(_sessions) - settings.chat_max_sessions
    if overflow > 0:
        by_age = sorted(_sessions.items(), key=lambda kv: kv[1].last_used)
        for key, _ in by_age:
            if overflow <= 0:
                break
            if key == protect:
                continue
            del _sessions[key]
            overflow -= 1


def _get_session(tenant_id: uuid.UUID, session_id: str) -> _Session:
    with _lock:
        now = time_mod.monotonic()
        key = (tenant_id, session_id)
        session = _sessions.setdefault(key, _Session())
        session.last_used = now
        _evict_locked(now, protect=key)
        return session


def delete_session(tenant_id: uuid.UUID, session_id: str) -> None:
    with _lock:
        _sessions.pop((tenant_id, session_id), None)


def clear_sessions(tenant_id: uuid.UUID | None = None) -> None:
    with _lock:
        if tenant_id is None:
            _sessions.clear()
            return
        for key in [k for k in _sessions if k[0] == tenant_id]:
            del _sessions[key]


NO_ANSWER = "Не удалось сформировать ответ — попробуйте переформулировать вопрос."


def run_chat_stream(
    session_id: str,
    message: str,
    db: Session,
    site: Site,
    role: str = "default",
) -> Iterator[dict]:
    tz = analytics.site_tz(site)
    zones = analytics.list_zones(db, site)
    session = _get_session(site.tenant_id, session_id)
    session.messages.append(Msg("user", [TextPart(message)]))

    if not is_configured(role):
        turn = _fallback_turn(session_id, message, db, site, zones, tz, session)
        yield {"type": "delta", "text": turn.answer_text}
        yield _done(turn)
        return

    log.info("chat[%s] USER (%s): %s", session_id[:8], role, message)
    ctx = tools.ToolContext(db=db, site=site, zones=zones, tz=tz)
    system = SYSTEM_PROMPT.format(
        zones=", ".join(f'"{z.name}" — {z.kind}' for z in zones) or "none",
        tz=site.timezone,
        now=datetime.now(tz).strftime("%Y-%m-%d %H:%M (%A)"),
        closing=site.closing_time.strftime("%H:%M"),
    )
    provider = get_provider(role)
    trace: list[ToolCallTrace] = []
    answer = ""

    for _round in range(settings.chat_max_tool_rounds):
        streamed = False
        resp = None
        for event in provider.stream(
            system=system, messages=session.messages, tools=tools.TOOL_SPECS
        ):
            if isinstance(event, TextDelta):
                streamed = True
                yield {"type": "delta", "text": event.text}
            else:
                resp = event.response
        if resp is None:
            resp = LLMResponse(text="")
        log.info(
            "chat[%s] r%d LLM: text=%r calls=%s",
            session_id[:8],
            _round,
            resp.text[:400],
            [(c.name, c.args) for c in resp.tool_calls],
        )
        if not resp.tool_calls:
            answer = resp.text
            break
        if streamed:
            yield {"type": "reset"}
        session.messages.append(Msg("model", [ToolCallPart(c) for c in resp.tool_calls]))
        results: list = []
        for call in resp.tool_calls:
            yield {"type": "tool", "name": call.name, "args": call.args}
            result = tools.dispatch(call.name, call.args, ctx)
            log.info(
                "chat[%s]   TOOL %s(%s) -> %s", session_id[:8], call.name, call.args, _short(result)
            )
            trace.append(ToolCallTrace(name=call.name, args=call.args))
            results.append(ToolResultPart(name=call.name, result=result, id=call.id))
        session.messages.append(Msg("tool", results))
        if ctx.images_pending:
            session.messages.append(Msg("user", [TextPart("Кадры с камеры:"), *ctx.images_pending]))
            ctx.images_pending = []
    else:
        for event in provider.stream(system=system, messages=session.messages, tools=None):
            if isinstance(event, TextDelta):
                yield {"type": "delta", "text": event.text}
            else:
                answer = event.response.text

    if not answer:
        answer = NO_ANSWER
        yield {"type": "delta", "text": answer}
    log.info("chat[%s] ANSWER: %s", session_id[:8], answer)
    session.messages.append(Msg("model", [TextPart(answer)]))
    _strip_media(session)
    _compact(session)

    events = sorted(ctx.events_out.values(), key=lambda e: e["ts_start"])
    yield _done(
        ChatTurnOut(
            session_id=session_id,
            answer_text=answer,
            events=[ChatEventOut(**e) for e in events[: tools.EVENTS_IN_PAYLOAD]],
            clips=[ChatClipOut(**c) for c in ctx.clips_out],
            tool_calls=trace,
        )
    )


def _done(turn: ChatTurnOut) -> dict:
    return {"type": "done", "turn": turn.model_dump(mode="json")}


def run_chat(
    session_id: str, message: str, db: Session, site: Site, role: str = "default"
) -> ChatTurnOut:
    turn: dict | None = None
    for event in run_chat_stream(session_id, message, db, site, role):
        if event["type"] == "done":
            turn = event["turn"]
        elif event["type"] == "error":
            raise RuntimeError(event["message"])
    if turn is None:
        raise RuntimeError("Chat turn produced no result")
    return ChatTurnOut.model_validate(turn)


def _short(obj: object, n: int = 400) -> str:
    s = repr(obj)
    return s if len(s) <= n else s[:n] + "…"


def _strip_media(session: _Session) -> None:
    for msg in session.messages:
        if any(isinstance(p, ImagePart) for p in msg.parts):
            msg.parts = [p for p in msg.parts if not isinstance(p, ImagePart)]


def _compact(session: _Session) -> None:
    if len(session.messages) > settings.chat_max_history:
        msgs = session.messages[-settings.chat_max_history :]
        while msgs and (
            msgs[0].role == "tool" or any(isinstance(p, ToolCallPart) for p in msgs[0].parts)
        ):
            msgs.pop(0)
        session.messages = msgs


def _fallback_turn(
    session_id: str,
    question: str,
    db: Session,
    site: Site,
    zones: list[Zone],
    tz: ZoneInfo,
    session: _Session,
) -> ChatTurnOut:
    log.info("chat[%s] USER (degraded, no key): %s", session_id[:8], question)
    q = question.lower()

    if any(w in q for w in ("очеред", "navbat", "queue")):
        event_type = "queue_len"
    elif any(w in q for w in ("сколько времени", "как долго", "dwell", "провел", "провёл")):
        event_type = "dwell"
    elif any(w in q for w in ("больше всего", "пик", "peak", "occupancy", "занятост")):
        event_type = "occupancy"
    elif any(w in q for w in ("вышл", "выход", "chiqdi", "exit", "left ")):
        event_type = "exit"
    else:
        event_type = "entry"

    zone = None
    for z in zones:
        if z.name.lower() in q:
            zone = z
            break
    if zone is None:
        kind = None
        if any(w in q for w in ("склад", "ombor", "storeroom", "warehouse", "store room")):
            kind = "store_room"
        elif any(w in q for w in ("касс", "kassa", "checkout")):
            kind = "checkout_area"
        elif any(w in q for w in ("вход", "kirish", "entrance")):
            kind = "entrance"
        if kind:
            zone = next((z for z in zones if z.kind == kind), None)

    now = datetime.now(tz)
    today = datetime.combine(now.date(), time.min, tzinfo=tz)
    if "до обеда" in q:
        ts_from, ts_to = today, today + timedelta(hours=13)
    elif "после обеда" in q:
        ts_from, ts_to = today + timedelta(hours=13), now
    elif any(w in q for w in ("после закрытия", "закрыти")):
        ts_from = datetime.combine(now.date(), site.closing_time, tzinfo=tz)
        ts_to = today + timedelta(days=1, hours=6)
    elif any(w in q for w in ("вчера", "kecha", "yesterday")):
        ts_from, ts_to = today - timedelta(days=1), today
    else:
        ts_from, ts_to = today, now

    events = analytics.find_events(db, site, event_type, zone, ts_from, ts_to)
    zone_names = {z.id: z.name for z in zones}

    if not events:
        where = f' в зоне "{zone.name}"' if zone else ""
        answer = (
            f"По данным камер: событий типа «{event_type}»{where} "
            f"с {ts_from:%d.%m %H:%M} до {ts_to:%H:%M} не найдено."
        )
    else:
        facts = analytics.summarize_events(event_type, events, zone_names, tz)
        answer = "ИИ недоступен — данные напрямую из базы:\n" + facts

    log.info("chat[%s] ANSWER (degraded): %s", session_id[:8], answer)
    session.messages.append(Msg("model", [TextPart(answer)]))
    _compact(session)

    shown = events[: tools.EVENTS_IN_PAYLOAD]
    snapshots = analytics.snapshot_keys_for_events(db, site, [e.id for e in shown])
    events_out = [
        ChatEventOut(
            id=e.id,
            type=e.type,
            zone_name=zone_names.get(e.zone_id),
            ts_start=e.ts_start,
            attributes=e.attributes or {},
            snapshot_url=storage.presign_get(snapshots[e.id]) if e.id in snapshots else None,
        )
        for e in shown
    ]
    args: dict = {
        "event_type": event_type,
        "time_from": ts_from.isoformat(),
        "time_to": ts_to.isoformat(),
    }
    if zone:
        args["zone_name"] = zone.name
    return ChatTurnOut(
        session_id=session_id,
        answer_text=answer,
        degraded=True,
        events=events_out,
        tool_calls=[ToolCallTrace(name="find_events", args=args)],
    )
