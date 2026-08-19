import pytest

from app.i18n import DEFAULT_LOCALE, LANGUAGE_NAMES, LOCALES, from_accept_language, resolve_locale
from app.services.ai.commentary import digest_heading, system_prompt
from app.services.ai.report import report_system

pytestmark = pytest.mark.unit


class TestResolveLocale:
    @pytest.mark.parametrize("locale", LOCALES)
    def test_an_explicit_cookie_wins(self, locale):
        assert resolve_locale(locale, "en-GB,en;q=0.9") == locale

    def test_the_header_is_used_when_there_is_no_cookie(self):
        assert resolve_locale(None, "uz-UZ,uz;q=0.9,ru;q=0.8") == "uz"

    def test_region_subtags_and_case_are_tolerated(self):
        assert resolve_locale("UZ-Latn-UZ") == "uz"
        assert resolve_locale("ru_RU") == "ru"

    @pytest.mark.parametrize("value", [None, "", "de", "klingon", "../../etc/passwd", "<script>"])
    def test_anything_unrecognised_falls_back(self, value):
        assert resolve_locale(value) == DEFAULT_LOCALE

    def test_quality_weights_are_respected(self):
        assert from_accept_language("ru;q=0.2, uz;q=0.9") == "uz"

    def test_an_unsupported_first_choice_does_not_hide_a_supported_second(self):
        assert from_accept_language("de-DE,de;q=0.9,uz;q=0.5") == "uz"


class TestPrompts:
    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_report_prompt_names_the_target_language(self, locale):
        prompt = report_system(locale)
        assert LANGUAGE_NAMES[locale] in prompt
        for other in LOCALES:
            if other != locale:
                assert LANGUAGE_NAMES[other] not in prompt

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_commentary_prompt_names_the_target_language(self, locale):
        assert LANGUAGE_NAMES[locale] in system_prompt(locale)

    def test_no_prompt_hardcodes_russian_any_more(self):
        for build in (report_system, system_prompt):
            assert "in Russian" not in build("uz")
            assert "на русском" not in build("uz")

    def test_the_degraded_digest_heading_is_translated(self):
        headings = [digest_heading(locale) for locale in LOCALES]
        assert all(headings)
        assert len(set(headings)) == len(LOCALES)

    def test_an_unknown_locale_still_produces_a_usable_prompt(self):
        assert "{language}" not in report_system("klingon")
        assert LANGUAGE_NAMES["ru"] in report_system("klingon")
