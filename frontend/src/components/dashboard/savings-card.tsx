"use client";

import { formatAmount } from "@/components/pos/receipt-card";
import {
  Chip,
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import type { Savings } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useT } from "@/lib/locale";

const LINE_KEY = {
  queues: "savings.line.queues",
  after_hours: "savings.line.after_hours",
  deliveries: "savings.line.deliveries",
  pos: "savings.line.pos",
} as const;

export function SavingsCard({ savings }: { savings: Savings }) {
  const t = useT();
  const currency = t("savings.currency");
  return (
    <PanelCard data-testid="savings-card">
      <PanelHeader>
        <PanelTitleRow>
          <PanelTitle>{t("savings.title")}</PanelTitle>
          <Chip>{savings.month}</Chip>
        </PanelTitleRow>
        <PanelSubtitle>{t("savings.subtitle")}</PanelSubtitle>
      </PanelHeader>

      <PanelBody className="flex flex-col gap-3 lg:flex-row lg:items-start lg:gap-6">
        <div className="shrink-0">
          <div
            className={cn(
              "text-[40px] leading-10 font-semibold tabular-nums",
              savings.net >= 0 ? "text-emerald-600" : "text-danger-ink",
            )}
          >
            {savings.net >= 0 ? "+" : "−"}
            {formatAmount(Math.abs(savings.net))}
            <span className="text-ink-muted pl-1.5 text-sm font-normal">{currency}</span>
          </div>
          <div className="text-ink-muted text-xs">{t("savings.net")}</div>
        </div>

        <div className="min-w-0 flex-1">
          <dl className="flex flex-col">
            {savings.lines.map((line) => (
              <div
                key={line.key}
                className="border-hairline flex items-baseline gap-2 border-b py-1.5 last:border-b-0"
              >
                <dt className="min-w-0 flex-1 truncate text-sm">{t(LINE_KEY[line.key])}</dt>

                <dd className="text-ink-muted text-xs tabular-nums">
                  {line.count * line.unit_value === line.amount
                    ? `${line.count} × ${formatAmount(line.unit_value)}`
                    : `× ${line.count}`}
                </dd>
                <dd className="w-28 text-right text-sm font-semibold tabular-nums">
                  {formatAmount(line.amount)}
                </dd>
              </div>
            ))}
            <div className="flex items-baseline gap-2 py-1.5">
              <dt className="min-w-0 flex-1 text-sm font-medium">{t("savings.prevented")}</dt>
              <dd className="w-28 text-right text-sm font-semibold tabular-nums">
                {formatAmount(savings.total)}
              </dd>
            </div>
            <div className="flex items-baseline gap-2 py-1.5">
              <dt className="text-ink-muted min-w-0 flex-1 text-sm">{t("savings.subscription")}</dt>
              <dd className="text-ink-muted w-28 text-right text-sm tabular-nums">
                −{formatAmount(savings.subscription)}
              </dd>
            </div>
          </dl>
          <p className="text-ink-faint mt-1 text-[11px] leading-4">{t("savings.formulaNote")}</p>
        </div>
      </PanelBody>
    </PanelCard>
  );
}
