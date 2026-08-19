import { describe, expect, it } from "vitest";
import { DEFAULT_LOCALE, LOCALE_NAMES, LOCALES, isLocale, localeFromCookie } from "./i18n";

describe("locale selection", () => {
  it("accepts every locale the catalog ships", () => {
    for (const locale of LOCALES) expect(localeFromCookie(locale)).toBe(locale);
  });

  it.each([undefined, "", "de", "ru-RU", "../etc/passwd", "<script>"])(
    "falls back to the default for %s",
    (value) => {
      expect(localeFromCookie(value as string | undefined)).toBe(DEFAULT_LOCALE);
    },
  );

  it("narrows with isLocale rather than trusting the string", () => {
    expect(isLocale("uz")).toBe(true);
    expect(isLocale("klingon")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });

  it("names every language in that language", () => {
    expect(LOCALE_NAMES.uz).toBe("Oʻzbekcha");
    expect(LOCALE_NAMES.ru).toBe("Русский");
    expect(Object.keys(LOCALE_NAMES).sort()).toEqual([...LOCALES].sort());
  });
});
