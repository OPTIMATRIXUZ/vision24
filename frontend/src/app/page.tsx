"use client";

import { ChevronDown, PlayIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  AddCircleIcon,
  CalendarMarkIcon,
  ClockCircleIcon,
  CloseCircleIcon,
  RefreshCircleIcon,
} from "@/components/icons";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DeliveryTrips } from "@/components/delivery-trips";
import { ErrorNote } from "@/components/error-note";
import { GlowBackdrop } from "@/components/glow-backdrop";
import { MediaViewer } from "@/components/media-viewer";
import {
  Chip,
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import { PillButton, pillVariants } from "@/components/pill-button";
import { RulesEditor } from "@/components/rules-editor";
import { Skeleton } from "@/components/ui/skeleton";
import { ZoneEditor } from "@/components/zone-editor";
import {
  analyzeAll,
  analyzeSource,
  captureNow,
  createDemoSource,
  deleteSource,
  getDeliveries,
  getProcessedVideo,
  getSources,
  resetEverything,
  reuploadSource,
  stopCapture,
  whoAmI,
  type DeliverySummary,
  type Source,
} from "@/lib/api";
import { cn } from "@/lib/utils";

import { useT, type TFunc } from "@/lib/locale";

const POLL_MS = 3000;

const KIND_STYLES: Record<string, string> = {
  entrance: "bg-[#dff3e3] text-[#136c2e]",
  checkout_area: "bg-[#ffecd1] text-[#ae1d00]",
  store_room: "bg-[#ffe2e2] text-[#a01212]",
  dining: "bg-[#e2ecff] text-[#12429f]",
  truck: "bg-[#fdeacd] text-[#8a4b06]",
  delivery_door: "bg-[#f1e4ff] text-[#6b21a8]",
  custom: "bg-chip text-ink-muted",
};

const kindLabels = (t: TFunc): Record<string, string> => ({
  entrance: t("zones.kind.entrance"),
  checkout_area: t("zones.kind.checkout_area"),
  store_room: t("zones.kind.store_room"),
  dining: t("zones.kind.dining"),
  truck: t("zones.kind.truck"),
  delivery_door: t("zones.kind.delivery_door"),
  custom: t("zones.kind.custom"),
});

function jobLabel(t: TFunc, s: Source): string | null {
  if (!s.job) return null;
  switch (s.job.state) {
    case "queued":
      return s.job.position > 1 ? t("job.queuedAt", { position: s.job.position }) : t("job.queued");
    case "capturing":
      return t("job.capturing");
    case "running":
      return t("job.running", {
        percent: Math.round(s.job.progress * 100),
        events: s.job.events_written,
      });
    case "error":
      return t("job.failed", { reason: s.job.error ?? t("job.unknownError") });
    default:
      return null;
  }
}

function relativeTime(t: TFunc, iso: string | null): string {
  if (!iso) return t("time.never");
  const diffS = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diffS < 90) return t("time.justNow");
  if (diffS < 3600) return t("time.minutesAgo", { count: Math.round(diffS / 60) });
  if (diffS < 86400) return t("time.hoursAgo", { count: Math.round(diffS / 3600) });
  return new Date(iso).toLocaleString();
}

export default function SourcesPage() {
  const t = useT();
  const [sources, setSources] = useState<Source[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const [tenantSlug, setTenantSlug] = useState("");
  useEffect(() => {
    whoAmI().then((w) => setTenantSlug(w?.tenant_slug ?? ""));
  }, []);
  const [busy, setBusy] = useState<string | null>(null);
  const [editingZones, setEditingZones] = useState<string | null>(null);
  const [videoModal, setVideoModal] = useState<{ url: string; name: string } | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const reuploadTarget = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function refresh() {
    try {
      setSources(await getSources());
      setError(null);
    } catch (e) {
      setError(e);
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  async function act(cameraId: string, fn: () => Promise<unknown>) {
    setBusy(cameraId);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  function pickReupload(cameraId: string) {
    reuploadTarget.current = cameraId;
    fileInput.current?.click();
  }

  async function onReuploadFile(file: File) {
    const target = reuploadTarget.current;
    if (!target) return;
    await act(target, async () => {
      await reuploadSource(target, file);
      await analyzeSource(target);
    });
  }

  async function watchProcessed(s: Source) {
    try {
      const { url } = await getProcessedVideo(s.camera_id);
      setVideoModal({ url, name: s.name });
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <GlowBackdrop />

      <input
        ref={fileInput}
        type="file"
        accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,video/webm"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onReuploadFile(f);
          e.target.value = "";
        }}
      />

      <MediaViewer
        open={videoModal !== null}
        onOpenChange={(next) => !next && setVideoModal(null)}
        caption={videoModal ? t("sources.processedVideo", { name: videoModal.name }) : undefined}
      >
        {videoModal && (
          <video
            src={videoModal.url}
            controls
            autoPlay
            className="max-h-[85vh] max-w-[92vw] rounded-[16px]"
          />
        )}
      </MediaViewer>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl leading-7 font-semibold">{t("nav.sources")}</h1>
        <div className="flex flex-wrap items-center gap-1.5">
          <PillButton
            variant="quiet"
            onClick={() => act("*", analyzeAll)}
            disabled={!sources?.length}
            title={t("sources.reanalyzeAllHint")}
          >
            {t("sources.reanalyzeAll")}
            <RefreshCircleIcon />
          </PillButton>
          <PillButton
            variant="dangerQuiet"
            onClick={() => setConfirmReset(true)}
            disabled={!sources?.length}
          >
            {t("sources.clearEverything")}
            <CloseCircleIcon className="text-danger-ink" />
          </PillButton>
          <Link href="/sources/new" className={pillVariants({ variant: "primary" })}>
            {t("sources.addSource")}
            <AddCircleIcon />
          </Link>
        </div>
      </div>

      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title={t("sources.resetTitle")}
        description={t("sources.resetBody")}
        confirmLabel={t("sources.clearEverything")}

        confirmPhrase={tenantSlug}
        onConfirm={() => act("*", () => resetEverything(tenantSlug))}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => !next && setDeleteTarget(null)}
        title={t("sources.deleteTitle", { name: deleteTarget?.name ?? "" })}
        description={t("sources.deleteBody")}
        confirmLabel={t("sources.delete")}
        onConfirm={async () => {
          if (deleteTarget) await act(deleteTarget.id, () => deleteSource(deleteTarget.id));
        }}
      />

      <ErrorNote error={error} />

      <div className="grid gap-4 lg:grid-cols-2">
        {sources === null &&
          [0, 1].map((i) => (
            <PanelCard key={i} className="min-h-[204px]">
              <PanelHeader>
                <Skeleton className="h-6 w-44" />
                <Skeleton className="h-4 w-56" />
              </PanelHeader>
              <PanelBody>
                <Skeleton className="h-[38px] w-full max-w-[319px]" />
              </PanelBody>
            </PanelCard>
          ))}

        {sources?.length === 0 && (
          <EmptyState onDemo={() => act("*", createDemoSource)} busy={busy === "*"} />
        )}

        {sources?.map((s) => {
          const label = jobLabel(t, s);
          const active = !!s.job && !["done", "error"].includes(s.job.state);
          const locked = active || busy === s.camera_id;
          const editing = editingZones === s.camera_id;
          return (
            <PanelCard
              key={s.camera_id}
              className={cn("min-h-[204px]", editing && "lg:col-span-2")}
            >
              <PanelHeader>
                <PanelTitleRow>
                  <PanelTitle>{s.name}</PanelTitle>
                  {s.zones.map((z) => (
                    <Chip key={z.id} className={KIND_STYLES[z.kind] ?? KIND_STYLES.custom}>
                      {kindLabels(t)[z.kind] ?? z.kind}
                    </Chip>
                  ))}
                  <Chip className="text-foreground">
                    {s.source_type === "cctv" ? t("sources.cctv") : t("sources.file")}
                  </Chip>
                </PanelTitleRow>
                <PanelSubtitle>
                  {t("sources.summary", {
                    when: relativeTime(t, s.last_analyzed),
                    entries: s.entries_count,
                    events: s.events_count,
                  })}
                </PanelSubtitle>
                {s.source_type === "cctv" && s.rtsp_url && (
                  <p className="text-ink-faint truncate font-mono text-[11px]">{s.rtsp_url}</p>
                )}
              </PanelHeader>

              {label && (
                <PanelBody>
                  <div
                    className={cn(
                      "flex flex-col gap-2 rounded-[8px] border px-3 py-2 text-xs",
                      s.job?.state === "error"
                        ? "border-danger-line bg-danger-line/5 text-danger-ink"
                        : "border-hairline bg-neutral-50",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span>{label}</span>
                      {s.job?.state === "capturing" && (
                        <PillButton
                          variant="neutral"
                          className="h-7 px-2 text-xs"
                          onClick={() => stopCapture(s.camera_id).then(refresh).catch(setError)}
                          title={t("sources.stopCaptureHint")}
                        >
                          {t("sources.stopCapture")}
                        </PillButton>
                      )}
                    </div>
                    {s.job?.state === "running" && (
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-200">
                        <div
                          className="bg-brand h-full transition-all"
                          style={{ width: `${Math.round((s.job.progress ?? 0) * 100)}%` }}
                        />
                      </div>
                    )}
                  </div>
                </PanelBody>
              )}

              <PanelBody>
                {s.source_type === "upload" ? (
                  <AnalyzeControls
                    disabled={locked}
                    onAnalyze={(endsAt) =>
                      act(s.camera_id, () => analyzeSource(s.camera_id, endsAt))
                    }
                  />
                ) : (
                  <CaptureControls
                    disabled={locked}
                    onCapture={(sec) => act(s.camera_id, () => captureNow(s.camera_id, sec))}
                  />
                )}
              </PanelBody>

              <PanelBody>
                <div className="bg-hairline h-px w-full" />
              </PanelBody>

              <PanelBody>
                <div className="flex flex-wrap items-center gap-2">
                  {s.source_type === "upload" && (
                    <PillButton
                      variant="neutral"
                      onClick={() => pickReupload(s.camera_id)}
                      disabled={locked}
                    >
                      {t("sources.reupload")}
                    </PillButton>
                  )}
                  {s.has_processed && (
                    <PillButton variant="neutral" onClick={() => watchProcessed(s)}>
                      {t("sources.watchProcessed")}
                    </PillButton>
                  )}
                  <div className="ml-auto flex flex-wrap items-center gap-2">
                    <PillButton
                      variant="info"
                      onClick={() => setEditingZones(editing ? null : s.camera_id)}
                    >
                      {editing ? t("sources.closeZones") : t("sources.editZones")}
                    </PillButton>
                    <PillButton
                      variant="danger"
                      onClick={() => setDeleteTarget({ id: s.camera_id, name: s.name })}
                      disabled={locked}
                    >
                      {t("sources.delete")}
                    </PillButton>
                  </div>
                </div>
              </PanelBody>

              {editing && (
                <PanelBody className="border-hairline flex flex-col gap-3 border-t pt-4">
                  <ZoneEditor cameraId={s.camera_id} />
                  <p className="text-ink-muted text-xs">{t("zones.applyNote")}</p>
                  <RulesEditor cameraId={s.camera_id} />
                </PanelBody>
              )}

              {s.zones.some((z) => z.kind === "truck") && (
                <SourceDeliveries cameraId={s.camera_id} />
              )}
            </PanelCard>
          );
        })}

        {!!sources?.length && <AddSourceTile />}
      </div>
    </div>
  );
}

function SourceDeliveries({ cameraId }: { cameraId: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState<DeliverySummary | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next) {
      try {
        setSummary(await getDeliveries(undefined, cameraId));
        setError(null);
      } catch (e) {
        setError(e);
      }
    }
  }

  return (
    <PanelBody className="border-hairline flex flex-col gap-3 border-t pt-4">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="text-foreground flex items-center gap-1.5 self-start text-sm font-medium"
      >
        <ChevronDown className={cn("size-4 transition-transform", !open && "-rotate-90")} />
        {t("deliveries.title")}
        {summary && open && (
          <span className="text-ink-muted font-normal">
            · {t("deliveries.tripCount", { count: summary.trips.length })}
          </span>
        )}
      </button>
      {open && <ErrorNote error={error} />}
      {open && summary && <DeliveryTrips trips={summary.trips} />}
    </PanelBody>
  );
}

function AddSourceTile() {
  const t = useT();
  return (
    <PanelCard className="min-h-[204px] justify-between">
      <PanelHeader>
        <PanelTitleRow>
          <PanelTitle>{t("sources.addTitle")}</PanelTitle>
          <Chip>{t("sources.addExample")}</Chip>
        </PanelTitleRow>
        <PanelSubtitle>{t("sources.addBlurb")}</PanelSubtitle>
      </PanelHeader>
      <PanelBody>
        <Link href="/sources/new" className={pillVariants({ variant: "neutral" })}>
          {t("sources.addSource")}
          <AddCircleIcon />
        </Link>
      </PanelBody>
    </PanelCard>
  );
}

function EmptyState({ onDemo, busy }: { onDemo: () => void; busy: boolean }) {
  const t = useT();
  return (
    <PanelCard className="min-h-[300px] items-center justify-center text-center lg:col-span-2">
      <PanelBody className="flex flex-col items-center gap-2">
        <span className="bg-chip/60 mb-2 flex size-12 items-center justify-center rounded-full">
          <AddCircleIcon className="size-6" />
        </span>
        <h2 className="text-[20px] leading-tight font-semibold">{t("sources.emptyTitle")}</h2>
        <p className="text-ink-muted max-w-md text-xs leading-4 font-medium">
          {t("sources.emptyBody")}
        </p>
      </PanelBody>
      <PanelBody>
        <Link href="/sources/new" className={pillVariants({ variant: "primary" })}>
          {t("sources.addSource")}
          <AddCircleIcon />
        </Link>
      </PanelBody>
      <PanelBody>
        <div className="border-brand/25 bg-brand/5 flex max-w-md flex-col items-center gap-3 rounded-[14px] border px-8 py-5">
          <p className="text-sm leading-5 font-medium">{t("sources.demoHint")}</p>
          <PillButton variant="dark" onClick={onDemo} disabled={busy}>
            {busy ? t("sources.demoStarting") : t("sources.tryDemo")}
            <PlayIcon className="size-4 fill-current" />
          </PillButton>
        </div>
      </PanelBody>
    </PanelCard>
  );
}

function IconField({
  icon,
  className,
  ...props
}: React.ComponentProps<"input"> & { icon: React.ReactNode }) {
  return (
    <label className="relative inline-flex">
      <input
        className={cn(
          "border-hairline text-ink-muted focus-visible:ring-brand/40 h-[38px] rounded-[8px] border bg-white pr-9 pl-[11px] text-sm font-medium outline-none focus-visible:ring-2 disabled:opacity-50",

          "[&::-webkit-calendar-picker-indicator]:absolute [&::-webkit-calendar-picker-indicator]:inset-y-0 [&::-webkit-calendar-picker-indicator]:right-0 [&::-webkit-calendar-picker-indicator]:w-9 [&::-webkit-calendar-picker-indicator]:cursor-pointer [&::-webkit-calendar-picker-indicator]:opacity-0",
          className,
        )}
        {...props}
      />
      <span className="text-foreground pointer-events-none absolute top-1/2 right-[11px] -translate-y-1/2">
        {icon}
      </span>
    </label>
  );
}

function AnalyzeControls({
  disabled,
  onAnalyze,
}: {
  disabled: boolean;
  onAnalyze: (endsAt?: string) => void;
}) {
  const t = useT();
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");

  const endsAt = date ? `${date}T${time || "00:00"}` : undefined;

  return (
    <div className="flex flex-wrap items-center gap-2" title={t("sources.endsAtHint")}>
      <IconField
        type="date"
        icon={<CalendarMarkIcon />}
        className="w-[139px]"
        value={date}
        onChange={(e) => setDate(e.target.value)}
        disabled={disabled}
        aria-label={t("sources.endsAtDate")}
      />
      <IconField
        type="time"
        icon={<ClockCircleIcon />}
        className="w-[96px]"
        value={time}
        onChange={(e) => setTime(e.target.value)}
        disabled={disabled}
        aria-label={t("sources.endsAtTime")}
      />
      <PillButton variant="dark" onClick={() => onAnalyze(endsAt)} disabled={disabled}>
        {t("sources.analyze")}
      </PillButton>
    </div>
  );
}

function CaptureControls({
  disabled,
  onCapture,
}: {
  disabled: boolean;
  onCapture: (seconds: number) => void;
}) {
  const t = useT();
  const [seconds, setSeconds] = useState(120);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        className="border-hairline text-ink-muted focus-visible:ring-brand/40 h-[38px] rounded-[8px] border bg-white px-[11px] text-sm font-medium outline-none focus-visible:ring-2 disabled:opacity-50"
        value={seconds}
        onChange={(e) => setSeconds(Number(e.target.value))}
        disabled={disabled}
        aria-label={t("sources.captureDuration")}
      >
        <option value={60}>{t("sources.dur60")}</option>
        <option value={120}>{t("sources.dur120")}</option>
        <option value={300}>{t("sources.dur300")}</option>
      </select>
      <PillButton variant="dark" onClick={() => onCapture(seconds)} disabled={disabled}>
        {t("sources.captureAnalyze")}
      </PillButton>
    </div>
  );
}
