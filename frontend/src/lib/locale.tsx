"use client";

import { createContext, useCallback, useContext } from "react";
import { DEFAULT_LOCALE, t as translate, type Locale, type MessageKey } from "@/lib/i18n";

const LocaleContext = createContext<Locale>(DEFAULT_LOCALE);

export function LocaleProvider({
  locale,
  children,
}: {
  locale: Locale;
  children: React.ReactNode;
}) {
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

export function useLocale(): Locale {
  return useContext(LocaleContext);
}

export type TFunc = (key: MessageKey, vars?: Record<string, string | number>) => string;

export function useT(): TFunc {
  const locale = useLocale();
  return useCallback(
    (key: MessageKey, vars?: Record<string, string | number>) => translate(key, vars, locale),
    [locale],
  );
}
