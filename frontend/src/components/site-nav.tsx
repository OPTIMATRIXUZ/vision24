"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

import { useT, type TFunc } from "@/lib/locale";

const navItems = (t: TFunc) => [
  { href: "/", label: t("nav.sources") },
  { href: "/live", label: t("nav.live") },
  { href: "/dashboard", label: t("nav.dashboard") },
  { href: "/pos", label: t("nav.pos") },
  { href: "/ask", label: t("nav.ask") },
  { href: "/report", label: t("nav.report") },
  { href: "/settings", label: t("nav.settings") },
];

export function SiteNav() {
  const t = useT();
  const pathname = usePathname();

  return (
    <nav
      aria-label={t("nav.label")}
      className="no-scrollbar flex min-w-0 items-center gap-4 overflow-x-auto px-2 py-[3px] text-sm leading-7 md:gap-8 md:text-base"
    >
      {navItems(t).map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "hover:text-foreground focus-visible:ring-ring rounded-sm whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:outline-none",
              active ? "text-foreground" : "text-ink-muted",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
