"use client";

import { DeliveryTrips } from "@/components/delivery-trips";
import {
  Chip,
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import type { DeliverySummary } from "@/lib/api";

import { useT } from "@/lib/locale";

export function DeliveriesCard({ summary }: { summary: DeliverySummary }) {
  const t = useT();
  return (
    <PanelCard>
      <PanelHeader>
        <PanelTitleRow>
          <PanelTitle>{t("deliveries.title")}</PanelTitle>
          <Chip>{t("deliveries.tripCount", { count: summary.trips.length })}</Chip>
        </PanelTitleRow>
        <PanelSubtitle>{t("deliveries.subtitle")}</PanelSubtitle>
      </PanelHeader>

      <PanelBody className="flex flex-wrap gap-2">
        {summary.totals.map((total) => (
          <div
            key={`${total.product_type_id ?? total.product_name}`}
            className="border-hairline flex min-w-[140px] flex-col rounded-[8px] border px-3 py-2"
          >
            <span className="text-ink-muted truncate text-xs">{total.product_name}</span>
            <span className="text-[22px] leading-7 font-semibold tabular-nums">
              {total.packages}
              <span className="text-ink-muted pl-1 text-xs font-normal">
                {t("deliveries.packagesShort")}
              </span>
            </span>
            {total.units != null && (
              <span className="text-ink-muted text-[11px] leading-4">
                ≈ {total.units} {total.unit_label || t("deliveries.units")}
              </span>
            )}
          </div>
        ))}
        {summary.unmatched_packages > 0 && (
          <div className="border-hairline flex min-w-[140px] flex-col rounded-[8px] border border-dashed px-3 py-2">
            <span className="text-ink-muted truncate text-xs">{t("deliveries.unmatched")}</span>
            <span className="text-[22px] leading-7 font-semibold tabular-nums">
              {summary.unmatched_packages}
            </span>
          </div>
        )}
      </PanelBody>

      <PanelBody>
        <DeliveryTrips trips={summary.trips} />
      </PanelBody>
    </PanelCard>
  );
}
