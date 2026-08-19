"use client";

import { formatAmount } from "@/components/pos/receipt-card";
import { Badge } from "@/components/ui/badge";
import type { PosVisit } from "@/lib/api";

import { useT } from "@/lib/locale";

function timeOf(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function VisitsList({
  visits,
  onOpen,
}: {
  visits: PosVisit[];
  onOpen: (url: string, caption?: string) => void;
}) {
  const t = useT();
  if (visits.length === 0) {
    return <p className="text-ink-muted text-xs">{t("pos.noVisits")}</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {visits.map((v) => {
        const range = `${timeOf(v.ts_start)}–${timeOf(v.ts_end)}`;
        return (
          <li
            key={`${v.ts_start}-${v.zone_name}`}
            data-testid="visit"
            className="flex items-start gap-3 rounded-[8px] border border-[var(--hairline)] p-3"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{range}</span>
                {v.kind === "administrative" && (
                  <Badge variant="secondary">{t("pos.visitAdministrative")}</Badge>
                )}
                {v.kind === "sale" && v.items.length === 0 && (
                  <span className="text-ink-muted text-xs">{t("pos.goodsNotVisible")}</span>
                )}
              </div>
              {v.items.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {v.items.map((i) => (
                    <span key={i.name} className="bg-chip rounded-[4px] px-1.5 py-0.5 text-xs">
                      {i.name} ×{i.qty}
                    </span>
                  ))}
                </div>
              )}
              <p className="text-ink-muted mt-0.5 font-mono text-xs">
                {v.receipt
                  ? `${v.receipt.external_id} · ${formatAmount(v.receipt.total)} ${t("savings.currency")}`
                  : t("pos.visitNoReceipt")}
              </p>
            </div>
            {v.snapshot_url && (
              <button
                type="button"
                onClick={() => onOpen(v.snapshot_url!, range)}
                title={t("pos.evidence")}
                className="shrink-0 cursor-zoom-in"
              >
                <img
                  src={v.snapshot_url}
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
