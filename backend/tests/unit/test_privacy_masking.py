import numpy as np
import pytest

from app.services.frames import apply_privacy_masks

pytestmark = [pytest.mark.unit]


class Zone:
    def __init__(self, polygon, privacy_mask=True, id="z1"):
        self.polygon = polygon
        self.privacy_mask = privacy_mask
        self.id = id


def gradient_frame(h=120, w=160):
    xs = np.linspace(0, 255, w, dtype=np.uint8)
    ys = np.linspace(0, 255, h, dtype=np.uint8)
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :, 0] = xs[None, :]
    frame[:, :, 1] = ys[:, None]
    frame[:, :, 2] = (xs[None, :] // 2) + (ys[:, None] // 2)
    return frame


def unchanged(a, b) -> bool:
    return np.array_equal(a, b)


def changed_fraction(a, b) -> float:
    return float(np.any(a != b, axis=-1).mean())


class TestFailsClosed:
    @pytest.mark.parametrize(
        "polygon",
        [
            "not a polygon",
            [[0.1, 0.1]],
            [[0.1, 0.1], [0.9, 0.1]],
            [[0.1], [0.2], [0.3]],
            [["a", "b"], ["c", "d"], ["e", "f"]],
            None,
        ],
        ids=["string", "one-point", "two-points", "1d-points", "non-numeric", "none"],
    )
    def test_an_unusable_polygon_obscures_the_whole_frame(self, polygon):
        original = gradient_frame()
        frame = original.copy()

        apply_privacy_masks(frame, [Zone(polygon)])

        assert changed_fraction(frame, original) > 0.9, (
            "a zone flagged privacy_mask was left substantially unmasked — this is "
            "the frame that gets uploaded to the VLM"
        )

    def test_one_bad_zone_does_not_let_the_others_through(self):
        original = gradient_frame()
        frame = original.copy()
        good = Zone([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]])

        apply_privacy_masks(frame, [good, Zone("broken")])

        assert not unchanged(frame[-10:, -10:], original[-10:, -10:])


class TestNormalOperation:
    def test_a_valid_zone_masks_only_its_own_region(self):
        original = gradient_frame()
        frame = original.copy()
        top_left = Zone([[0.0, 0.0], [0.3, 0.0], [0.3, 0.3], [0.0, 0.3]])

        apply_privacy_masks(frame, [top_left])

        assert not unchanged(frame[:20, :20], original[:20, :20]), "the zone was not masked"
        assert unchanged(frame[-20:, -20:], original[-20:, -20:]), "an unflagged area was masked"

    def test_zones_without_the_flag_are_untouched(self):
        original = gradient_frame()
        frame = original.copy()
        ordinary = Zone([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]], privacy_mask=False)

        apply_privacy_masks(frame, [ordinary])

        assert unchanged(frame, original)

    def test_no_zones_at_all_is_a_no_op(self):
        original = gradient_frame()
        frame = original.copy()

        apply_privacy_masks(frame, [])

        assert unchanged(frame, original)
