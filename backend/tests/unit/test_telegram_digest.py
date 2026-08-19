from app.services.telegram import format_digest

CONTEXT = {
    "date": "2026-08-03",
    "site": "Demo Store",
    "entries_total": 42,
    "peak_occupancy": {"value": 7, "time": "13:40"},
    "queues": [{"zone": "Касса", "max_queue_len": 5, "breaches": [{"from": "13:00"}]}],
    "alerts": [{"time": "13:02", "message": "queue", "value": 5}],
    "after_hours_entries": [{"time": "22:10:00", "zone": "Вход"}],
    "deliveries": {"trips": 3, "products": [], "unmatched_packages": 0},
    "pos": {"receipts": 12, "discrepancies": [{"flag": "no_person_at_sale"}]},
    "savings": {"month": "2026-08", "total": 1_552_000, "net": 52_000},
}


class TestFormatDigest:
    def test_carries_every_section_with_data(self):
        text = format_digest(CONTEXT, "ru")
        assert "Demo Store" in text and "2026-08-03" in text
        assert "Посетителей: 42" in text
        assert "Пик: 7" in text
        assert "Касса" in text and "превышений: 1" in text
        assert "Входов после закрытия: 1" in text
        assert "Поставки: 3" in text
        assert "Подозрительных кассовых операций: 1" in text
        assert "1 552 000" in text and "52 000" in text

    def test_stays_short(self):
        assert len(format_digest(CONTEXT, "ru").splitlines()) < 25

    def test_empty_day_says_so(self):
        empty = dict(CONTEXT, entries_total=0, alerts=[])
        assert "записей нет" in format_digest(empty, "ru")

    def test_locales(self):
        assert "Visitors: 42" in format_digest(CONTEXT, "en")
        assert "Tashrif: 42" in format_digest(CONTEXT, "uz")
        assert "Посетителей" in format_digest(CONTEXT, "de")
