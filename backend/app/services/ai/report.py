import json
import logging
import threading
import uuid
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.i18n import DEFAULT_LOCALE, LANGUAGE_NAMES
from app.models import Site
from app.schemas import ReportOut
from app.services import analytics
from app.services.ai.provider import Msg, TextPart, get_provider, is_configured

log = logging.getLogger(__name__)

REPORT_SYSTEM_TEMPLATE = """You are a retail CCTV analyst. Write the manager's daily report
in {language}, as Markdown. Use ONLY the numbers in the supplied data — invent nothing.
Structure, with the headings translated into {language}: ## Summary (3 bullets),
## Footfall, ## Queues, ## Zones, ## Deliveries (only if the data has delivery trips —
state trips, per-product package counts and derived units), ## POS reconciliation (only
if the data has receipts — state the receipt count and each discrepancy with its time
and amount; the feed is a simulated Flowpos integration, mention that once), ## Savings
(only if data.savings has lines — the month's prevented-loss estimate: give each line as
count × amount and the net vs the subscription, in UZS), ## Incidents,
## Recommendations (2-3 bullets drawn from the data). Give times in local time.
250-400 words. If there is no data for the day, say plainly that there are no records."""


def report_system(locale: str) -> str:
    return REPORT_SYSTEM_TEMPLATE.format(language=LANGUAGE_NAMES.get(locale, LANGUAGE_NAMES["ru"]))


_cache: dict[tuple[uuid.UUID, str, str], ReportOut] = {}
_lock = threading.Lock()


def clear_cache(site_id: uuid.UUID | None = None) -> None:
    with _lock:
        if site_id is None:
            _cache.clear()
            return
        for key in [k for k in _cache if k[0] == site_id]:
            del _cache[key]


def generate_report(
    db: Session,
    site: Site,
    day: date | None,
    refresh: bool = False,
    locale: str = DEFAULT_LOCALE,
) -> ReportOut:
    tz = analytics.site_tz(site)
    if day is None:
        day = datetime.now(tz).date()
    day_key = day.isoformat()
    key = (site.id, day_key, locale)
    with _lock:
        if not refresh and key in _cache:
            return _cache[key]

    context = analytics.build_report_context(db, site, day)

    if is_configured():
        user_text = (
            f"Data for {context['date']} ({context['site']}, TZ {context['timezone']}, "
            f"closing at {context['closing_time']}):\n{json.dumps(context, ensure_ascii=False)}"
        )
        markdown = (
            get_provider()
            .generate(
                system=report_system(locale),
                messages=[Msg("user", [TextPart(user_text)])],
            )
            .text
        )
        generated_by = settings.ai_provider
    else:
        markdown = _render_template(context)
        generated_by = "fallback"

    report = ReportOut(
        day=day_key,
        markdown=markdown,
        data=context,
        generated_by=generated_by,
        generated_at=datetime.now(UTC),
    )
    with _lock:
        _cache[key] = report
    return report


def _render_template(c: dict) -> str:
    lines = [f"# Отчёт за {c['date']} — {c['site']}", "", "## Сводка"]
    lines.append(f"- Всего входов: **{c['entries_total']}**")
    peak = c["peak_occupancy"]
    if peak["value"]:
        lines.append(f"- Пик посетителей: **{peak['value']}** в {peak['time']}")
    lines.append(f"- Срабатываний алертов: **{len(c['alerts'])}**")

    if c["hourly_traffic"]:
        lines += ["", "## Посещаемость (по часам)", "| Час | Входы |", "|---|---|"]
        lines += [f"| {b['hour']} | {b['entries']} |" for b in c["hourly_traffic"]]

    if c["queues"]:
        lines += ["", "## Очереди"]
        for q in c["queues"]:
            threshold = f", порог {q['threshold']}" if q["threshold"] else ""
            lines.append(f"- {q['zone']}: макс. длина {q['max_queue_len']}{threshold}")
            for b in q["breaches"]:
                lines.append(f"  - превышение с {b['from']} до {b['to']} (пик {b['peak']})")

    active_zones = [
        z for z in c["zones"] if z["entries"] or z["avg_dwell_s"] or z["peak_occupancy"]
    ]
    if active_zones:
        lines += ["", "## Зоны", "| Зона | Входы | Пик | Ср. время (с) |", "|---|---|---|---|"]
        lines += [
            f"| {z['name']} | {z['entries']} | {z['peak_occupancy']} | {z['avg_dwell_s']} |"
            for z in active_zones
        ]

    deliveries = c.get("deliveries") or {}
    if deliveries.get("trips"):
        lines += ["", "## Поставки"]
        lines.append(f"- Рейсов от машины в магазин: **{deliveries['trips']}**")
        for p in deliveries["products"]:
            units = f" (~{p['units']} {p['unit_label'] or 'шт'})" if p["units"] else ""
            lines.append(f"- {p['name']}: **{p['packages']}** упаковок{units}")
        if deliveries.get("unmatched_packages"):
            lines.append(f"- Нераспознанных упаковок: {deliveries['unmatched_packages']}")

    pos = c.get("pos") or {}
    if pos.get("receipts"):
        flag_ru = {
            "no_person_at_sale": "продажа при пустой кассе",
            "void_no_customer": "отмена без покупателя",
            "unscanned_visit": "покупатель без чека",
        }
        lines += ["", "## Сверка с кассой"]
        lines.append(f"- Чеков за день: **{pos['receipts']}** (симуляция интеграции Flowpos)")
        if pos["discrepancies"]:
            lines.append(f"- Подозрительных операций: **{len(pos['discrepancies'])}**")
            for d in pos["discrepancies"]:
                amount = f", {d['total_uzs']:,} сум".replace(",", " ") if d["total_uzs"] else ""
                lines.append(f"  - {d['time']} — {flag_ru.get(d['flag'], d['flag'])}{amount}")
        else:
            lines.append("- Расхождений с камерой не найдено")

    savings = c.get("savings") or {}
    if savings.get("lines"):
        line_ru = {
            "queues": "предотвращённые уходы из очереди",
            "after_hours": "входы после закрытия",
            "deliveries": "нераспознанные упаковки в поставках",
            "pos": "подозрительные кассовые операции",
        }
        lines += ["", "## Сэкономлено"]
        for entry in savings["lines"]:
            amount = f"{entry['amount']:,}".replace(",", " ")
            lines.append(
                f"- {line_ru.get(entry['key'], entry['key'])} ({entry['count']}): **{amount} сум**"
            )
        total = f"{savings['total']:,}".replace(",", " ")
        net = f"{savings['net']:,}".replace(",", " ")
        sub = f"{savings['subscription']:,}".replace(",", " ")
        lines.append(
            f"- Итого за {savings['month']}: **{total} сум** "
            f"(подписка {sub} сум, чистыми {net} сум)"
        )

    if c["alerts"]:
        lines += ["", "## Инциденты"]
        lines += [f"- {a['time']} — {a['message']}" for a in c["alerts"]]

    if c["after_hours_entries"]:
        lines += ["", "## Входы после закрытия"]
        lines += [f"- {e['time']} — {e['zone']}" for e in c["after_hours_entries"]]

    if c["entries_total"] == 0 and not c["alerts"]:
        lines += ["", "_За этот день записей нет — загрузите и проанализируйте видео._"]

    return "\n".join(lines)
