"use client";

import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/error-note";
import {
  Chip,
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import { ProductCatalog } from "@/components/product-catalog";
import { TelegramSettings } from "@/components/telegram-settings";
import { PillButton } from "@/components/pill-button";
import { Input } from "@/components/ui/input";
import { getSite, updateSite } from "@/lib/api";

import { useT } from "@/lib/locale";

export default function SettingsPage() {
  const t = useT();
  const [timezone, setTimezone] = useState("");
  const [closing, setClosing] = useState("21:00");
  const [siteName, setSiteName] = useState("");
  const [saved, setSaved] = useState(false);

  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSite()
      .then((s) => {
        setSiteName(s.name);
        setTimezone(s.timezone);
        setClosing(s.closing_time.slice(0, 5));
      })
      .catch(setError);
  }, []);

  async function save() {
    setBusy(true);
    setSaved(false);
    setError(null);
    try {
      const s = await updateSite(timezone.trim(), closing);
      setTimezone(s.timezone);
      setClosing(s.closing_time.slice(0, 5));
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl leading-7 font-semibold">{t("nav.settings")}</h1>
        {siteName && <Chip>{siteName}</Chip>}
      </div>

      <ErrorNote error={error} />

      <PanelCard className="max-w-2xl">
        <PanelHeader>
          <PanelTitleRow>
            <PanelTitle>{t("settings.title")}</PanelTitle>
          </PanelTitleRow>
          <PanelSubtitle>{t("settings.subtitle")}</PanelSubtitle>
        </PanelHeader>

        <PanelBody className="flex flex-col gap-5">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="timezone" className="text-sm font-medium">
              {t("settings.timezone")}
            </label>
            <Input
              id="timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              placeholder="Asia/Tashkent"
              className="max-w-xs"
            />
            <p className="text-ink-muted text-xs leading-4">{t("settings.timezoneHint")}</p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="closing-time" className="text-sm font-medium">
              {t("settings.closingTime")}
            </label>
            <Input
              id="closing-time"
              type="time"
              value={closing}
              onChange={(e) => setClosing(e.target.value)}
              className="w-36"
            />
            <p className="text-ink-muted text-xs leading-4">{t("settings.closingTimeHint")}</p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <PillButton variant="primary" onClick={save} disabled={busy || !timezone.trim()}>
              {busy ? t("settings.saving") : t("settings.save")}
            </PillButton>
            {saved && (
              <p role="status" aria-live="polite" className="text-ink-muted text-xs leading-4">
                {t("settings.saved")}
              </p>
            )}
          </div>
        </PanelBody>
      </PanelCard>

      <TelegramSettings />

      <ProductCatalog />
    </div>
  );
}
