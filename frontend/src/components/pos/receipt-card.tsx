"use client";

import { Badge } from "@/components/ui/badge";
import type { PosReceipt } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useT } from "@/lib/locale";

export function formatAmount(value: number): string {
  return value.toLocaleString("ru-RU");
}

const KIND_KEY = { sale: "pos.sale", void: "pos.void", refund: "pos.refund" } as const;

export function ReceiptCard({ receipt }: { receipt: PosReceipt }) {
  const t = useT();
  const voided = receipt.kind === "void";
  return (
    <div
      data-testid="receipt-card"
      className={cn(
        "border-hairline flex flex-col gap-1 rounded-[8px] border px-3 py-2 font-mono text-xs",
        receipt.flag && "border-danger-line bg-danger-line/5",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="text-foreground font-semibold">{receipt.external_id}</span>
        <Badge variant={voided || receipt.kind === "refund" ? "destructive" : "secondary"}>
          {t(KIND_KEY[receipt.kind])}
        </Badge>
        {receipt.source === "simulated" && (
          <span className="text-ink-faint text-[10px]">{t("pos.simulated")}</span>
        )}
        <span className="text-ink-muted ml-auto tabular-nums">
          {new Date(receipt.ts).toLocaleTimeString()}
        </span>
      </div>

      {receipt.items.length > 0 && (
        <ul className={cn("flex flex-col", voided && "line-through opacity-60")}>
          {receipt.items.map((item) => (
            <li key={item.sku} className="flex items-baseline gap-2">
              <span className="min-w-0 flex-1 truncate">{item.name}</span>
              <span className="text-ink-muted tabular-nums">
                {item.qty} × {formatAmount(item.unit_price)}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="border-hairline flex items-baseline justify-between border-t pt-1">
        <span className="text-ink-muted">{t("pos.total")}</span>
        <span className={cn("font-semibold tabular-nums", voided && "line-through")}>
          {formatAmount(receipt.total)} {t("savings.currency")}
        </span>
      </div>
    </div>
  );
}
