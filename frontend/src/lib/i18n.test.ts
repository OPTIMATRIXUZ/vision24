import { describe, expect, it } from "vitest";
import { DEFAULT_LOCALE, LOCALES, MESSAGE_KEYS, t } from "./i18n";

describe("message catalog", () => {
  it("defaults to English", () => {
    expect(DEFAULT_LOCALE).toBe("en");
    expect(t("nav.sources")).toBe("Sources");
  });

  it("substitutes placeholders", () => {
    expect(t("live.onScreen", { count: 3 })).toBe("On screen now: 3");
    expect(t("live.queue", { zone: "Checkout", count: 2 })).toBe("Checkout: queue 2");
  });

  it("leaves an unknown placeholder visible rather than blanking it", () => {
    expect(t("live.queue", { zone: "Checkout" })).toBe("Checkout: queue {count}");
  });

  it("renders the key itself when a message is missing", () => {
    // @ts-expect-error deliberately not a MessageKey
    expect(t("nope.not.a.key")).toBe("nope.not.a.key");
  });

  it.each(LOCALES)("locale %s defines every key", (locale) => {
    const missing = MESSAGE_KEYS.filter((key) => t(key, undefined, locale) === key);
    expect(missing).toEqual([]);
  });

  it.each(LOCALES)("locale %s keeps every placeholder the message needs", (locale) => {
    const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

    const wrong = MESSAGE_KEYS.filter((key) => {
      const expected = placeholders(t(key, undefined, "en"));
      return String(placeholders(t(key, undefined, locale))) !== String(expected);
    });
    expect(wrong).toEqual([]);
  });

  it("translates Uzbek rather than falling through to another language", () => {
    expect(t("nav.sources", undefined, "uz")).toBe("Manbalar");
    expect(t("nav.sources", undefined, "uz")).not.toBe(t("nav.sources", undefined, "ru"));
    expect(t("nav.sources", undefined, "uz")).not.toBe(t("nav.sources", undefined, "en"));
  });

  it("keeps Russian available in the switcher", () => {
    expect(t("nav.sources", undefined, "ru")).toBe("Источники");
  });
});
