"use client";

import { useEffect, useState } from "react";
import { Lightbox, type LightboxState } from "@/components/lightbox";
import {
  Chip,
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import { PillButton } from "@/components/pill-button";
import { DiscrepancyList } from "@/components/pos/discrepancy-list";
import { ReceiptCard } from "@/components/pos/receipt-card";
import { VisitsList } from "@/components/pos/visits-list";
import {
  getPosDiscrepancies,
  getPosReceipts,
  getPosVisits,
  simulatePos,
  type PosDiscrepancies,
  type PosReceipt,
  type PosVisit,
} from "@/lib/api";

import { useT } from "@/lib/locale";

const POLL_MS = 10_000;

export default function PosPage() {
  const t = useT();
  const [receipts, setReceipts] = useState<PosReceipt[]>([]);
  const [recon, setRecon] = useState<PosDiscrepancies | null>(null);
  const [visits, setVisits] = useState<PosVisit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);

  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [feed, out, seen] = await Promise.all([
          getPosReceipts(),
          getPosDiscrepancies(),
          getPosVisits(),
        ]);
        if (cancelled) return;
        setReceipts(feed);
        setRecon(out);
        setVisits(seen);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    }
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [reloadKey]);

  const simulate = async () => {
    setSimulating(true);
    setError(null);
    try {
      await simulatePos();
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setSimulating(false);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      {lightbox && (
        <Lightbox url={lightbox.url} caption={lightbox.caption} onClose={() => setLightbox(null)} />
      )}
      {error && (
        <PanelCard className="border-destructive/30 bg-destructive/5">
          <PanelBody className="text-destructive text-sm">{error}</PanelBody>
        </PanelCard>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl leading-7 font-semibold">{t("pos.title")}</h1>
          <Chip className="border border-amber-300 bg-amber-50 text-amber-700">
            {t("pos.simBadge")}
          </Chip>
        </div>
        <PillButton
          variant="primary"
          onClick={simulate}
          disabled={simulating}
          title={t("pos.simulateHint")}
        >
          {simulating ? t("pos.simulating") : t("pos.simulate")}
        </PillButton>
      </div>
      <p className="text-ink-muted text-sm">{t("pos.subtitle")}</p>

      <div className="flex flex-col gap-2 lg:flex-row">
        <PanelCard className="flex-1">
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("pos.feed")}</PanelTitle>
              <Chip>{t("pos.receiptCount", { count: receipts.length })}</Chip>
            </PanelTitleRow>
          </PanelHeader>
          <PanelBody className="flex flex-col gap-2">
            {receipts.length === 0 ? (
              <p className="text-ink-muted text-xs">{t("pos.noReceipts")}</p>
            ) : (
              receipts.map((r) => <ReceiptCard key={r.id} receipt={r} />)
            )}
          </PanelBody>
        </PanelCard>

        <PanelCard className="flex-1">
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("pos.suspicious")}</PanelTitle>
              {recon && recon.discrepancies.length > 0 && (
                <Chip className="bg-danger-line/10 text-danger-ink">
                  {recon.discrepancies.length}
                </Chip>
              )}
            </PanelTitleRow>
          </PanelHeader>
          <PanelBody className="flex flex-col gap-2">
            {recon != null && recon.unverified_receipts > 0 && (
              <div className="rounded-[12px] border border-amber-300 bg-amber-50 px-3 py-2">
                <p className="text-sm font-medium text-amber-800">
                  {t("pos.unverified", { count: recon.unverified_receipts })}
                </p>
                <p className="mt-0.5 text-xs text-amber-700">{t("pos.unverifiedHint")}</p>
              </div>
            )}
            <DiscrepancyList
              discrepancies={recon?.discrepancies ?? []}
              partial={(recon?.unverified_receipts ?? 0) > 0}
              onOpen={(url, caption) => setLightbox({ url, caption })}
            />
          </PanelBody>
        </PanelCard>
      </div>

      <PanelCard>
        <PanelHeader>
          <PanelTitleRow>
            <PanelTitle>{t("pos.visits")}</PanelTitle>
            {visits.length > 0 && <Chip>{visits.length}</Chip>}
          </PanelTitleRow>
          <PanelSubtitle>{t("pos.visitsHint")}</PanelSubtitle>
        </PanelHeader>
        <PanelBody>
          <VisitsList visits={visits} onOpen={(url, caption) => setLightbox({ url, caption })} />
        </PanelBody>
      </PanelCard>
    </div>
  );
}
