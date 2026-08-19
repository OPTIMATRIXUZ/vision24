import numpy as np
import pytest

from worker.motion import MotionGate

pytestmark = pytest.mark.unit

SIZE = 100
TOTAL_PIXELS = SIZE * SIZE


def blank():
    return np.zeros((SIZE, SIZE, 3), dtype=np.uint8)


def with_changed_pixels(n: int):
    frame = blank()
    flat = frame.reshape(-1, 3)
    flat[:n] = 255
    return flat.reshape(SIZE, SIZE, 3)


def test_first_frame_is_always_motion():
    assert MotionGate().motion(blank()) is True


def test_identical_frames_are_static():
    gate = MotionGate()
    gate.motion(blank())
    assert gate.motion(blank()) is False


def test_resolution_change_forces_detection():
    gate = MotionGate()
    gate.motion(blank())
    taller = np.zeros((SIZE * 2, SIZE, 3), dtype=np.uint8)
    assert gate.motion(taller) is True


def test_threshold_boundary_is_inclusive():
    gate = MotionGate(min_ratio=0.003)
    gate.motion(blank())
    assert gate.motion(with_changed_pixels(30)) is True

    gate2 = MotionGate(min_ratio=0.003)
    gate2.motion(blank())
    assert gate2.motion(with_changed_pixels(29)) is False


def test_change_below_pixel_delta_does_not_count():
    gate = MotionGate(pixel_delta=25)
    gate.motion(blank())
    faint = np.full((SIZE, SIZE, 3), 20, dtype=np.uint8)
    assert gate.motion(faint) is False


def test_reference_updates_every_call_so_gradual_drift_is_tracked():
    gate = MotionGate(min_ratio=0.003)
    gate.motion(blank())
    gate.motion(with_changed_pixels(5000))
    assert gate.motion(with_changed_pixels(5000)) is False


def test_ratio_is_relative_to_the_downscaled_frame():
    gate = MotionGate()
    wide = np.zeros((480, 640, 3), dtype=np.uint8)
    assert gate.motion(wide) is True
    assert gate.motion(wide.copy()) is False
