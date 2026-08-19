import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services.frames import read_frames_at

N_FRAMES = 40
W = H = 64


def _level(idx: int) -> int:
    return 20 + idx * 5


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> str:
    raw = tmp_path_factory.mktemp("rf") / "raw.avi"
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (W, H))
    if not writer.isOpened():
        pytest.skip("no MJPG writer available")
    for i in range(N_FRAMES):
        writer.write(np.full((H, W, 3), _level(i), np.uint8))
    writer.release()

    out = tmp_path_factory.mktemp("rf2") / "clip.mp4"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not out.exists():
        pytest.skip("ffmpeg unavailable")
    return str(out)


def _observed(frame) -> float:
    return float(frame.mean())


class TestReadFramesAt:
    def test_returns_the_requested_frames(self, video):
        wanted = [0, 3, 11, 27, 39]
        got = list(read_frames_at(video, wanted))
        assert [i for i, _ in got] == wanted
        for idx, frame in got:
            assert abs(_observed(frame) - _level(idx)) < 6, f"frame {idx} is not frame {idx}"

    def test_matches_a_plain_sequential_decode(self, video):
        cap = cv2.VideoCapture(video)
        truth = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            truth.append(f)
        cap.release()

        wanted = [1, 2, 8, 19, 30]
        for idx, frame in read_frames_at(video, wanted):
            assert np.array_equal(frame, truth[idx]), f"frame {idx} differs from sequential decode"

    def test_unsorted_and_duplicated_indices_collapse(self, video):
        got = list(read_frames_at(video, [12, 4, 12, 4, 30]))
        assert [i for i, _ in got] == [4, 12, 30]

    def test_every_frame_can_be_requested(self, video):
        got = list(read_frames_at(video, range(N_FRAMES)))
        assert [i for i, _ in got] == list(range(N_FRAMES))
        for idx, frame in got:
            assert abs(_observed(frame) - _level(idx)) < 6

    def test_indices_past_the_end_are_dropped_not_raised(self, video):
        got = list(read_frames_at(video, [5, N_FRAMES + 500]))
        assert [i for i, _ in got] == [5]

    def test_no_indices_reads_nothing(self, video):
        assert list(read_frames_at(video, [])) == []
        assert list(read_frames_at(video, [-3, -1])) == []

    def test_never_seeks(self, video, monkeypatch):
        seeks: list[float] = []
        real = cv2.VideoCapture

        class Recording:
            def __init__(self, *args, **kwargs):
                self._cap = real(*args, **kwargs)

            def set(self, prop, value):
                if prop == cv2.CAP_PROP_POS_FRAMES:
                    seeks.append(value)
                return self._cap.set(prop, value)

            def __getattr__(self, name):
                return getattr(self._cap, name)

        monkeypatch.setattr(cv2, "VideoCapture", Recording)
        got = list(read_frames_at(video, [2, 17, 33]))

        assert [i for i, _ in got] == [2, 17, 33], "wrapper broke the read"
        assert seeks == [], f"read_frames_at seeked to {seeks} instead of decoding in order"

    def test_unopenable_path_raises(self):
        with (
            tempfile.NamedTemporaryFile(suffix=".mp4") as empty,
            pytest.raises(RuntimeError, match="Cannot open video"),
        ):
            list(read_frames_at(empty.name, [0]))

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="Cannot open video"):
            list(read_frames_at(tmp_path / "nope.mp4", [0]))
