import pytest

from app.config import Settings
from scripts.gen_env_example import TARGET, render

pytestmark = pytest.mark.unit

SECRET_SUFFIXES = ("_api_key", "_secret_key", "_password")


def _documented_keys() -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in TARGET.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


def test_env_example_is_in_sync_with_settings():
    assert TARGET.read_text() == render(), (
        "\n.env.example is stale. Regenerate it:\n"
        "    cd backend && python -m scripts.gen_env_example --write\n"
    )


def test_every_setting_is_documented():
    missing = {n.upper() for n in Settings.model_fields} - _documented_keys()
    assert not missing, f"settings with no .env.example entry: {sorted(missing)}"


def test_no_key_is_documented_that_does_not_exist():
    extra = _documented_keys() - {n.upper() for n in Settings.model_fields}
    assert not extra, f"documented but not a setting: {sorted(extra)}"


def test_secrets_are_blank():
    text = TARGET.read_text()
    for name in Settings.model_fields:
        if any(name.endswith(s) for s in SECRET_SUFFIXES):
            assert f"{name.upper()}=\n" in text + "\n", f"{name.upper()} should have no value"
