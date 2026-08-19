"use client";

import { formatAmount } from "@/components/pos/receipt-card";
import { Badge } from "@/components/ui/badge";
import type { PosDiscrepancy } from "@/lib/api";

import { useT } from "@/lib/locale";

const FLAG_KEY = {
  no_person_at_sale: "pos.flag.no_person_at_sale",
  void_no_customer: "pos.flag.void_no_customer",
  unscanned_visit: "pos.flag.unscanned_visit",
} as const;

function timeOf(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function DiscrepancyList({
  discrepancies,
  onOpen,
  partial = false,
}: {
  discrepancies: PosDiscrepancy[];
  onOpen: (url: string, caption?: string) => void;

  partial?: boolean;
}) {
  const t = useT();
  if (discrepancies.length === 0) {
    return (
      <p className="text-ink-muted text-xs">
        {partial ? t("pos.noSuspiciousPartial") : t("pos.noSuspicious")}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {discrepancies.map((d) => {
        const label = t(FLAG_KEY[d.flag]);
        const cleared = d.status === "cleared";
        const detail =
          d.flag === "unscanned_visit"
            ? t("pos.visitNoSale", { from: timeOf(d.ts), to: timeOf(d.ts_end ?? d.ts) })
            : t("pos.emptyCheckoutAt", { time: timeOf(d.ts) });
        return (
          <li
            key={`${d.flag}-${d.ts}-${d.receipt?.id ?? ""}`}
            data-testid="discrepancy"
            className={
              cleared
                ? "flex items-start gap-3 rounded-[8px] border border-[var(--hairline)] p-3 opacity-60"
                : "border-danger-line flex items-start gap-3 rounded-[8px] border p-3"
            }
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={cleared ? "secondary" : "destructive"}>{label}</Badge>
                {cleared && (
                  <span className="text-ink-muted text-xs font-medium">{t("pos.clearedByAi")}</span>
                )}
                {d.zone_name && <span className="text-ink-muted text-xs">{d.zone_name}</span>}
              </div>
              <p className="mt-1 text-sm">{detail}</p>
              {d.seen_items && d.seen_items.length > 0 && (
                <p className="mt-0.5 text-xs font-medium">
                  {t("pos.cameraSaw", {
                    items: d.seen_items.map((i) => `${i.name} ×${i.qty}`).join(", "),
                  })}
                </p>
              )}
              {d.receipt && (
                <p className="text-ink-muted mt-0.5 font-mono text-xs">
                  {d.receipt.external_id} · {formatAmount(d.receipt.total)} {t("savings.currency")}
                </p>
              )}
            </div>
            {d.snapshot_url && (
              <button
                type="button"
                onClick={() => onOpen(d.snapshot_url!, `${label} — ${detail}`)}
                title={t("pos.evidence")}
                className="shrink-0 cursor-zoom-in"
              >
                <img
                  src={d.snapshot_url}
                  alt={t("pos.evidence")}
                  loading="lazy"
                  className="h-[62px] w-[110px] rounded-[4px] border object-cover"
                />
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
