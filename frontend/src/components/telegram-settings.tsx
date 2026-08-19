"use client";

import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/error-note";
import {
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import { PillButton } from "@/components/pill-button";
import { Input } from "@/components/ui/input";
import {
  getTelegramSettings,
  sendTelegramDigest,
  sendTelegramTest,
  updateTelegramSettings,
} from "@/lib/api";

import { useT } from "@/lib/locale";

export function TelegramSettings() {
  const t = useT();
  const [chatId, setChatId] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [digestTime, setDigestTime] = useState("");
  const [botConfigured, setBotConfigured] = useState(true);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getTelegramSettings()
      .then((s) => {
        setChatId(s.chat_id ?? "");
        setEnabled(s.enabled);
        setDigestTime(s.digest_time ? s.digest_time.slice(0, 5) : "");
        setBotConfigured(s.bot_configured);
      })
      .catch(setError);
  }, []);

  const run = async (action: () => Promise<unknown>, doneMessage: string) => {
    setBusy(true);
    setStatus(null);
    setError(null);
    try {
      await action();
      setStatus(doneMessage);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(
      () =>
        updateTelegramSettings({
          chat_id: chatId.trim() || null,
          enabled,
          digest_time: digestTime ? `${digestTime}:00` : null,
        }),
      t("telegram.saved"),
    );

  return (
    <PanelCard className="max-w-2xl" data-testid="telegram-settings">
      <PanelHeader>
        <PanelTitleRow>
          <PanelTitle>{t("telegram.title")}</PanelTitle>
        </PanelTitleRow>
        <PanelSubtitle>{t("telegram.subtitle")}</PanelSubtitle>
      </PanelHeader>

      <PanelBody className="flex flex-col gap-5">
        {!botConfigured && (
          <p className="border-danger-line text-danger-ink rounded-[8px] border px-3 py-2 text-xs">
            {t("telegram.noBot")}
          </p>
        )}
        <ErrorNote error={error} />

        <div className="flex flex-col gap-1.5">
          <label htmlFor="tg-chat" className="text-sm font-medium">
            {t("telegram.chatId")}
          </label>
          <Input
            id="tg-chat"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            placeholder="-1001234567890"
            className="max-w-xs"
          />
          <p className="text-ink-muted text-xs leading-4">{t("telegram.chatIdHint")}</p>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            {t("telegram.enabled")}
          </label>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="tg-digest" className="text-sm font-medium">
            {t("telegram.digestTime")}
          </label>
          <Input
            id="tg-digest"
            type="time"
            value={digestTime}
            onChange={(e) => setDigestTime(e.target.value)}
            className="w-36"
          />
          <p className="text-ink-muted text-xs leading-4">{t("telegram.digestTimeHint")}</p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <PillButton variant="primary" onClick={save} disabled={busy}>
            {t("telegram.save")}
          </PillButton>
          <PillButton
            onClick={() => run(sendTelegramTest, t("telegram.testSent"))}
            disabled={busy || !botConfigured}
          >
            {t("telegram.test")}
          </PillButton>
          <PillButton
            onClick={() => run(sendTelegramDigest, t("telegram.digestSent"))}
            disabled={busy || !botConfigured}
          >
            {t("telegram.digestNow")}
          </PillButton>
          {status && (
            <p role="status" aria-live="polite" className="text-ink-muted text-xs leading-4">
              {status}
            </p>
          )}
        </div>
      </PanelBody>
    </PanelCard>
  );
}
