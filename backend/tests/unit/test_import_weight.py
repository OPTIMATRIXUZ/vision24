import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.unit

HEAVY = ("torch", "ultralytics", "open_clip")


def _modules_after_importing(target: str) -> set[str]:
    code = textwrap.dedent(f"""
        import sys, json
        import {target}
        print(json.dumps(sorted(m for m in sys.modules if "." not in m)))
    """)
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=180
    )
    import json

    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def test_api_does_not_import_the_ml_stack():
    loaded = _modules_after_importing("app.main")
    leaked = sorted(set(HEAVY) & loaded)
    assert not leaked, (
        f"app.main pulls in {leaked} at import time. Move the import inside the "
        f"function that needs it (see routers/videos.py submit_analysis)."
    )


def test_zone_engine_stays_dependency_free():
    loaded = _modules_after_importing("worker.zone_engine")
    leaked = sorted({*HEAVY, "cv2"} & loaded)
    assert not leaked, (
        f"worker.zone_engine pulls in {leaked}. Import shared dataclasses from "
        f"worker.types, not worker.detector."
    )


@pytest.mark.gpu
def test_worker_batch_still_imports():
    _modules_after_importing("worker.batch")


def test_the_bare_pytest_script_can_import_the_project():
    import subprocess
    import sys
    from pathlib import Path

    script = Path(sys.executable).parent / "pytest"
    if not script.exists():
        pytest.skip(f"no pytest console script beside {sys.executable}")

    backend = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [str(script), "--collect-only", "-q", "tests/unit/test_import_weight.py"],
        cwd=backend,
        capture_output=True,
        text=True,
    )
    assert "No module named" not in (result.stdout + result.stderr), result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
