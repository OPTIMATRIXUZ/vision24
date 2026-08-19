import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter_Tight } from "next/font/google";
import { cookies } from "next/headers";
import Link from "next/link";
import { LanguagePicker } from "@/components/language-picker";
import { SiteNav } from "@/components/site-nav";
import { SitePicker } from "@/components/site-picker";
import { LOCALE_COOKIE, localeFromCookie, t } from "@/lib/i18n";
import { LocaleProvider } from "@/lib/locale";
import "./globals.css";

const interTight = Inter_Tight({
  variable: "--font-sans",
  subsets: ["latin", "cyrillic"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-ibm-plex-mono",
  weight: ["400", "500"],
  subsets: ["latin", "cyrillic"],
});

export const metadata: Metadata = {
  title: "Vision 24",
  description: "AI video analytics for retail & HoReCa",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = localeFromCookie((await cookies()).get(LOCALE_COOKIE)?.value);

  return (
    <html
      lang={locale}
      className={`${interTight.variable} ${plexMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-neutral-50">
        <LocaleProvider locale={locale}>
          <a
            href="#main"
            className="focus-visible:ring-ring sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-3 focus-visible:left-3 focus-visible:z-50 focus-visible:rounded-lg focus-visible:bg-white focus-visible:px-3 focus-visible:py-2 focus-visible:text-sm focus-visible:ring-2"
          >
            {t("nav.skipToContent", undefined, locale)}
          </a>
          <header className="border-hairline sticky top-0 z-40 flex items-center justify-between gap-4 border-b bg-white/60 px-6 py-4 backdrop-blur-[10px] md:px-8 md:py-5">
            <Link
              href="/"
              className="to-brand-deep focus-visible:ring-ring shrink-0 rounded-sm bg-gradient-to-r from-black bg-clip-text text-2xl font-semibold text-transparent focus-visible:ring-2 md:text-[28px]"
            >
              Vision24
            </Link>
            <SiteNav />
            <LanguagePicker />
            <SitePicker />
          </header>

          <main id="main" className="mx-auto w-full max-w-[1048px] flex-1 px-6 py-8">
            {children}
          </main>
        </LocaleProvider>
      </body>
    </html>
  );
}
