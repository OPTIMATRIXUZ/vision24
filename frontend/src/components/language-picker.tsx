"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LOCALE_COOKIE, LOCALE_NAMES, LOCALES, isLocale, type Locale } from "@/lib/i18n";
import { useLocale, useT } from "@/lib/locale";

export function LanguagePicker() {
  const current = useLocale();
  const t = useT();

  function onChange(value: string | null) {
    if (!isLocale(value) || value === current) return;

    document.cookie = `${LOCALE_COOKIE}=${value}; path=/; max-age=31536000; samesite=lax`;
    window.location.reload();
  }

  return (
    <Select
      items={LOCALES.map((locale) => ({ value: locale, label: LOCALE_NAMES[locale] }))}
      value={current}
      onValueChange={onChange}
    >
      <SelectTrigger className="min-w-[11ch] shrink-0" aria-label={t("session.language")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {LOCALES.map((locale: Locale) => (
          <SelectItem key={locale} value={locale}>
            {LOCALE_NAMES[locale]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
