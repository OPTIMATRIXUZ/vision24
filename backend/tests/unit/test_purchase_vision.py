import typing
from types import SimpleNamespace

import numpy as np

from app.services import purchase_vision
from app.services.purchase_vision import (
    FRAME_FRACS,
    MAX_CATALOG_NAMES,
    REFERENCE_PHOTOS,
    VisitWindow,
    _name_items,
    _parse_items,
    _parse_verdict,
    _view_bbox,
    _view_jpeg,
    frame_indices,
    naming_parts,
)


def _verdict_json(**over) -> str:
    import json

    entry = {
        "visit": 1,
        "kind": "sale",
        "items": [{"name": "snack packet", "qty": 1}],
        "confidence": 0.9,
        "notes": "customer pays",
    }
    entry.update(over)
    return json.dumps({"visits": [entry]})


class TestParseVerdict:
    def test_clean_json(self):
        v = _parse_verdict(_verdict_json())
        assert v is not None
        assert v.kind == "sale"
        assert v.items == [{"name": "snack packet", "qty": 1}]
        assert v.confidence == 0.9

    def test_fenced_json_survives(self):
        v = _parse_verdict("```json\n" + _verdict_json(kind="administrative") + "\n```")
        assert v is not None
        assert v.kind == "administrative"

    def test_unknown_kind_is_rejected_not_guessed(self):
        assert _parse_verdict(_verdict_json(kind="probably_fine")) is None

    def test_no_json_at_all(self):
        assert _parse_verdict("I could not determine anything.") is None

    def test_items_are_sanitized(self):
        v = _parse_verdict(
            _verdict_json(
                items=[
                    {"name": "cola", "qty": 2},
                    {"name": "", "qty": 1},
                    {"name": "x" * 500, "qty": 1},
                    {"name": "bulk", "qty": 500},
                ]
            )
        )
        assert v is not None
        assert [i["qty"] for i in v.items] == [2, 1]
        assert len(v.items[1]["name"]) == 80

    def test_confidence_clamped(self):
        assert _parse_verdict(_verdict_json(confidence=7)).confidence == 1.0
        assert _parse_verdict(_verdict_json(confidence=-1)).confidence == 0.0

    def test_missing_items_key_means_no_items(self):
        v = _parse_verdict(_verdict_json(items=None))
        assert v is not None
        assert v.items == []


class TestFrameIndices:
    def test_edges_are_sampled(self):
        assert min(FRAME_FRACS) <= 0.05
        assert max(FRAME_FRACS) >= 0.95
        idxs = frame_indices(VisitWindow(zone_id=None, polygon=[], start_idx=1000, end_idx=2000))
        assert idxs[0] == 1050
        assert idxs[-1] == 1950
        assert idxs == sorted(idxs)

    def test_tiny_window_collapses_without_duplicates(self):
        idxs = frame_indices(VisitWindow(zone_id=None, polygon=[], start_idx=10, end_idx=12))
        assert idxs == sorted(set(idxs))
        assert all(10 <= i <= 12 for i in idxs)


class _FakeProvider:

    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls: list = []

    def generate(self, system, messages):
        self.calls.append(messages[0].parts)
        return SimpleNamespace(text=self.texts.pop(0))


class TestCatalogNaming:

    def test_classification_prompt_never_contains_the_catalog(self):
        assert "{catalog}" not in purchase_vision.PROMPT
        assert "known products include" not in purchase_vision.PROMPT

    def test_naming_prompt_carries_names_and_truncates(self):
        text = naming_parts(["Buldak лапша", "Кола 1.5л"])
        assert "Buldak лапша, Кола 1.5л" in text
        many = [f"p{i}" for i in range(MAX_CATALOG_NAMES + 20)]
        text = naming_parts(many)
        assert f"p{MAX_CATALOG_NAMES - 1}" in text
        assert f"p{MAX_CATALOG_NAMES}" not in text

    def test_parse_items(self):
        assert _parse_items('{"items": [{"name": "Кола 1.5л", "qty": 2}]}') == [
            {"name": "Кола 1.5л", "qty": 2}
        ]
        assert _parse_items("no json here") is None
        assert _parse_items('{"items": []}') == []

    def test_name_items_appends_capped_labeled_references(self):
        provider = _FakeProvider(['{"items": []}'])
        refs = [(f"prod{i}", b"\xff\xd8jpeg") for i in range(REFERENCE_PHOTOS + 2)]
        _name_items(provider, [b"\xff\xd8f1", b"\xff\xd8f2"], ["prod0"], refs, visit_no=0)

        (parts,) = provider.calls
        texts = [p.text for p in parts if hasattr(p, "text")]
        images = [p for p in parts if not hasattr(p, "text")]
        assert len(images) == 2 + REFERENCE_PHOTOS
        assert sum(1 for t in texts if t.startswith("Reference photo of")) == REFERENCE_PHOTOS

    def test_reference_cap_leaves_room_for_a_small_catalog(self):
        assert REFERENCE_PHOTOS >= 6

    def test_name_items_failure_returns_none_not_raises(self):
        class _Boom:
            def generate(self, system, messages):
                raise RuntimeError("provider down")

        assert _name_items(_Boom(), [b"f1", b"f2"], ["x"], [], visit_no=0) is None


class TestDescribeVisitsCatalogFlow:

    VISIT = VisitWindow(
        zone_id=None,
        polygon=[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
        start_idx=0,
        end_idx=100,
    )

    def _run(self, monkeypatch, texts, catalog):
        provider = _FakeProvider(texts)
        frame = np.full((240, 320, 3), 127, np.uint8)

        def fake_read(path, indices):
            for idx in sorted({int(i) for i in indices}):
                yield idx, frame.copy()

        monkeypatch.setattr(purchase_vision, "read_frames_at", fake_read)
        import app.services.ai.provider as provider_mod

        monkeypatch.setattr(provider_mod, "get_provider", lambda role="default": provider)
        verdicts = purchase_vision.describe_visits(
            "/fake.mp4", [self.VISIT], zones=[], catalog_names=catalog
        )
        return provider, verdicts

    def test_naming_call_items_replace_zero_shot_items(self, monkeypatch):
        provider, (verdict,) = self._run(
            monkeypatch,
            [
                '{"visits": [{"visit": 1, "kind": "sale", "items": '
                '[{"name": "yellow packet", "qty": 1}], "confidence": 0.9, "notes": ""}]}',
                '{"items": [{"name": "Buldak лапша", "qty": 1}]}',
            ],
            catalog=["Buldak лапша"],
        )
        assert len(provider.calls) == 2
        assert verdict.kind == "sale"
        assert verdict.items == [{"name": "Buldak лапша", "qty": 1}]

    def test_administrative_verdict_skips_the_naming_call(self, monkeypatch):
        provider, (verdict,) = self._run(
            monkeypatch,
            [
                '{"visits": [{"visit": 1, "kind": "administrative", "items": [], '
                '"confidence": 0.95, "notes": "paperwork"}]}',
            ],
            catalog=["Buldak лапша"],
        )
        assert len(provider.calls) == 1
        assert verdict.kind == "administrative"

    def test_empty_catalog_means_single_call(self, monkeypatch):
        provider, (verdict,) = self._run(
            monkeypatch,
            [
                '{"visits": [{"visit": 1, "kind": "sale", "items": '
                '[{"name": "bottle", "qty": 1}], "confidence": 0.9, "notes": ""}]}',
            ],
            catalog=[],
        )
        assert len(provider.calls) == 1
        assert verdict.items == [{"name": "bottle", "qty": 1}]

    def test_naming_garbage_keeps_zero_shot_items(self, monkeypatch):
        _provider, (verdict,) = self._run(
            monkeypatch,
            [
                '{"visits": [{"visit": 1, "kind": "sale", "items": '
                '[{"name": "bottle", "qty": 1}], "confidence": 0.9, "notes": ""}]}',
                "sorry, I cannot help with that",
            ],
            catalog=["Кола 1.5л"],
        )
        assert verdict.items == [{"name": "bottle", "qty": 1}]


class TestViewCrop:
    POLY: typing.ClassVar = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]

    def test_bbox_is_expanded_and_clamped(self):
        x1, y1, x2, y2 = _view_bbox(self.POLY, 1000, 1000)
        assert x1 < 400 and x2 > 600
        assert x1 >= 0 and y1 >= 0 and x2 <= 1000 and y2 <= 1000

    def test_edge_polygon_clamps_to_frame(self):
        x1, y1, _x2, _y2 = _view_bbox([[0.0, 0.0], [0.3, 0.0], [0.3, 0.3], [0.0, 0.3]], 1000, 1000)
        assert (x1, y1) == (0, 0)

    def test_jpeg_is_produced_and_downscaled(self):
        frame = np.full((1080, 1920, 3), 128, np.uint8)
        jpg = _view_jpeg(frame, self.POLY, zones=[])
        assert jpg is not None
        assert jpg[:2] == b"\xff\xd8"

    def test_degenerate_polygon_yields_none(self):
        frame = np.full((100, 100, 3), 128, np.uint8)
        assert _view_jpeg(frame, [[0.5, 0.5], [0.5, 0.5]], zones=[]) is None
