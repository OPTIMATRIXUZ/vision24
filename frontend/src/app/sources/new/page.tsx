"use client";

import { ArrowLeft, ArrowRight, ChevronDown } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useRef, useState } from "react";
import { GlowBackdrop } from "@/components/glow-backdrop";
import { LinkIcon, UploadIcon } from "@/components/icons";
import { PanelBody, PanelCard } from "@/components/panel-card";
import { PillButton, pillVariants } from "@/components/pill-button";
import { VideoPlayer } from "@/components/video-player";
import { ZoneEditor } from "@/components/zone-editor";
import {
  addCctvSource,
  addUploadSource,
  analyzeSource,
  api,
  captureNow,
  getProcessedVideo,
  testCctv,
  type AnalysisStatus,
  type ZoneKind,
} from "@/lib/api";

import { useT, type TFunc } from "@/lib/locale";
import { cn } from "@/lib/utils";

type SourceType = "upload" | "cctv";
type Step = "type" | "describe" | "zones" | "run";

const FIELD =
  "h-[38px] w-full rounded-[8px] border border-hairline bg-white px-[11px] text-sm font-medium text-foreground outline-none placeholder:text-ink-muted focus-visible:ring-2 focus-visible:ring-brand/40 disabled:opacity-50";

const kinds = (t: TFunc): { value: ZoneKind; label: string; hint: string }[] => [
  { value: "entrance", label: t("zones.kind.entrance"), hint: t("newSource.hint.entrance") },
  {
    value: "checkout_area",
    label: t("zones.kind.checkout_area"),
    hint: t("newSource.hint.checkout_area"),
  },
  { value: "store_room", label: t("zones.kind.store_room"), hint: t("newSource.hint.store_room") },
  { value: "dining", label: t("zones.kind.dining"), hint: t("newSource.hint.dining") },
  { value: "truck", label: t("zones.kind.truck"), hint: t("newSource.hint.truck") },
  {
    value: "delivery_door",
    label: t("zones.kind.delivery_door"),
    hint: t("newSource.hint.delivery_door"),
  },
  { value: "custom", label: t("zones.kind.custom"), hint: t("newSource.hint.custom") },
];

const steps = (t: TFunc): { key: Step; label: string }[] => [
  { key: "type", label: t("newSource.step.type") },
  { key: "describe", label: t("newSource.step.describe") },
  { key: "zones", label: t("newSource.step.zones") },
  { key: "run", label: t("newSource.step.run") },
];

function stagesFor(t: TFunc, sourceType: SourceType): string[] {
  return sourceType === "cctv"
    ? [t("newSource.stage.queued"), t("newSource.stage.capturing"), t("newSource.stage.analyzing")]
    : [t("newSource.stage.queued"), t("newSource.stage.analyzing")];
}

function stageIndex(t: TFunc, sourceType: SourceType, state: AnalysisStatus["state"]): number {
  if (state === "queued") return 0;
  if (state === "capturing") return 1;
  return stagesFor(t, sourceType).length - 1;
}

export default function NewSourcePage() {
  const t = useT();
  const [step, setStep] = useState<Step>("type");
  const [sourceType, setSourceType] = useState<SourceType>("upload");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ZoneKind>("entrance");
  const [file, setFile] = useState<File | null>(null);
  const [rtspUrl, setRtspUrl] = useState("");
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    snapshot?: string;
    error?: string;
  } | null>(null);
  const [testing, setTesting] = useState(false);
  const [advancedZones, setAdvancedZones] = useState(false);
  const [cameraId, setCameraId] = useState<string | null>(null);
  const [duration, setDuration] = useState(120);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [processedUrl, setProcessedUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => void (pollRef.current && clearInterval(pollRef.current)), []);

  const stepIndex = steps(t).findIndex((s) => s.key === step);

  async function testConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testCctv(rtspUrl.trim());
      setTestResult({ ok: r.ok, snapshot: r.snapshot_b64, error: r.error });
    } catch (e) {
      setTestResult({ ok: false, error: String(e) });
    } finally {
      setTesting(false);
    }
  }

  async function createSource() {
    setBusy(true);
    setError(null);
    try {
      const autoZone = !advancedZones;
      let id: string;
      if (sourceType === "upload") {
        if (!file) throw new Error(t("newSource.chooseFileFirst"));
        const r = await addUploadSource(file, name.trim(), kind, autoZone);
        id = r.camera_id;
      } else {
        const r = await addCctvSource({
          rtsp_url: rtspUrl.trim(),
          name: name.trim(),
          kind,
          auto_zone: autoZone,
        });
        id = r.camera_id;
      }
      setCameraId(id);
      setStep(advancedZones ? "zones" : "run");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function startRun() {
    if (!cameraId) return;
    setBusy(true);
    setError(null);
    setProcessedUrl(null);
    try {
      if (sourceType === "upload") {
        await analyzeSource(cameraId);
      } else {
        await captureNow(cameraId, duration);
      }
      pollRef.current = setInterval(async () => {
        try {
          const s = await api<AnalysisStatus>(`/api/videos/${cameraId}/status`);
          setStatus(s);
          if (s.state === "done" || s.state === "error") {
            if (pollRef.current) clearInterval(pollRef.current);
            if (s.state === "done") {
              getProcessedVideo(cameraId)
                .then((r) => setProcessedUrl(r.url))
                .catch(() => setProcessedUrl(null));
            }
          }
        } catch {}
      }, 1500);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const canDescribe =
    name.trim().length > 0 &&
    (sourceType === "upload" ? !!file : rtspUrl.trim().startsWith("rtsp"));

  return (
    <div className="flex flex-col gap-4">
      <GlowBackdrop />

      <input
        ref={fileInput}
        type="file"
        accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,video/webm"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0] ?? null;
          e.target.value = "";
          if (!f) return;
          setFile(f);
          setSourceType("upload");
          setStep("describe");
        }}
      />

      <div className="flex flex-wrap items-center gap-4">
        {steps(t).map((s, i) => (
          <Fragment key={s.key}>
            {i > 0 && <span aria-hidden className="bg-hairline h-px w-[52px] shrink-0" />}
            <span
              className={cn(
                "inline-flex size-[38px] shrink-0 items-center justify-center rounded-[20px] border text-[20px] font-medium",
                i <= stepIndex
                  ? "bg-brand-deep border-white text-white"
                  : "border-surface-border text-foreground bg-white",
              )}
            >
              {i + 1}
            </span>
            <span
              className={cn(
                "text-2xl leading-7 font-semibold",
                i === stepIndex ? "text-foreground" : "text-ink-muted",
              )}
            >
              {s.label}
            </span>
          </Fragment>
        ))}
      </div>

      {error && (
        <PanelCard className="border-danger-line bg-danger-line/5">
          <PanelBody className="text-danger-ink text-sm">{error}</PanelBody>
        </PanelCard>
      )}

      {step === "type" && (
        <div className="flex flex-wrap gap-2">
          <SourceTypeCard
            title={t("newSource.uploadTitle")}
            description={t("newSource.uploadBlurb")}
            icon={<UploadIcon className="text-brand" />}
            onClick={() => fileInput.current?.click()}
          />
          <SourceTypeCard
            title={t("newSource.cctvTitle")}
            description={t("newSource.cctvBlurb")}
            icon={<LinkIcon className="text-brand" />}
            onClick={() => {
              setSourceType("cctv");
              setStep("describe");
            }}
          />
        </div>
      )}

      {step === "describe" && (
        <>
          <h2 className="text-2xl leading-7 font-semibold">
            {sourceType === "upload" ? t("newSource.videoAndZone") : t("newSource.cameraAndZone")}
          </h2>
          <PanelCard>
            <PanelBody className="flex flex-col gap-2">
              <label htmlFor="zone-name" className="text-base font-semibold">
                {t("newSource.zoneName")}
              </label>
              <input
                id="zone-name"
                className={FIELD}
                placeholder={t("newSource.zoneNamePlaceholder")}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <p className="text-ink-muted text-xs leading-4 font-medium">
                {t("newSource.zoneNameHint")}
              </p>
            </PanelBody>

            <PanelBody className="flex flex-col gap-2">
              <span className="text-base font-semibold">{t("newSource.zoneKind")}</span>
              <div className="flex flex-wrap gap-1.5">
                {kinds(t).map((k) => (
                  <PillButton
                    key={k.value}
                    variant={kind === k.value ? "active" : "quiet"}
                    onClick={() => setKind(k.value)}
                    aria-pressed={kind === k.value}
                  >
                    {k.label}
                  </PillButton>
                ))}
              </div>
              <p className="text-ink-muted text-xs leading-4 font-medium">
                {kinds(t).find((k) => k.value === kind)?.hint}
              </p>
            </PanelBody>

            {sourceType === "upload" ? (
              <PanelBody className="flex flex-col gap-2">
                <span className="text-base font-semibold">{t("newSource.videoFile")}</span>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-ink-muted text-sm font-medium">
                    {file ? file.name : t("newSource.noFile")}
                  </span>
                  <PillButton variant="quiet" onClick={() => fileInput.current?.click()}>
                    {file ? t("newSource.chooseAnother") : t("newSource.chooseFile")}
                  </PillButton>
                </div>
              </PanelBody>
            ) : (
              <PanelBody className="flex flex-col gap-2">
                <label htmlFor="rtsp" className="text-base font-semibold">
                  {t("newSource.rtspUrl")}
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    id="rtsp"
                    className={cn(FIELD, "flex-1 font-mono text-xs")}
                    placeholder="rtsp://user:password@192.168.1.64:554/stream1"
                    value={rtspUrl}
                    onChange={(e) => {
                      setRtspUrl(e.target.value);
                      setTestResult(null);
                    }}
                  />
                  <PillButton
                    variant="quiet"
                    onClick={testConnection}
                    disabled={testing || !rtspUrl.trim().startsWith("rtsp")}
                  >
                    {testing ? t("newSource.testing") : t("newSource.testConnection")}
                  </PillButton>
                </div>
                {testResult && !testResult.ok && (
                  <p className="text-danger-ink text-xs">{testResult.error}</p>
                )}
                {testResult?.ok && testResult.snapshot && (
                  <div className="flex flex-col gap-1.5">
                    <p className="text-ink-muted text-xs font-medium">{t("newSource.connected")}</p>

                    <img
                      src={testResult.snapshot}
                      alt={t("newSource.cameraPreview")}
                      className="border-hairline max-h-56 w-fit rounded-[6px] border"
                    />
                  </div>
                )}
              </PanelBody>
            )}

            <PanelBody className="border-hairline flex items-center justify-between gap-2 border-t pt-4">
              <PillButton variant="neutral" onClick={() => setStep("type")}>
                <ArrowLeft className="size-5" />
                {t("newSource.back")}
              </PillButton>
              <PillButton variant="next" onClick={() => setStep("zones")} disabled={!canDescribe}>
                {t("newSource.next")}
                <ArrowRight className="size-5" />
              </PillButton>
            </PanelBody>
          </PanelCard>
        </>
      )}

      {step === "zones" && !cameraId && (
        <PanelCard>
          <PanelBody className="flex flex-col gap-2">
            <span className="text-base font-semibold">{t("newSource.zoneSetup")}</span>
            <label className="flex items-start gap-3 text-sm font-medium">
              <input
                type="checkbox"
                checked={!advancedZones}
                onChange={(e) => setAdvancedZones(!e.target.checked)}
                className="checked:border-brand-deep checked:bg-brand-deep mt-0.5 size-[18px] shrink-0 appearance-none rounded-[4px] border-[1.5px] border-[#999ea3] bg-white"
              />
              <span className="flex flex-col gap-1">
                {t("newSource.autoZone", { name: name || "…", kind })}
                <span className="text-ink-muted text-xs leading-4">
                  {t("newSource.autoZoneHint")}
                </span>
              </span>
            </label>
          </PanelBody>

          <PanelBody className="flex flex-wrap gap-1.5">
            {kinds(t).map((k) => (
              <PillButton
                key={k.value}
                variant={kind === k.value ? "active" : "quiet"}
                onClick={() => setKind(k.value)}
                aria-pressed={kind === k.value}
              >
                {k.label}
              </PillButton>
            ))}
          </PanelBody>

          {advancedZones && (
            <PanelBody>
              <p className="border-hairline text-ink-muted rounded-[8px] border bg-white px-[11px] py-2.5 text-sm font-medium">
                {t("newSource.advancedHint")}
              </p>
            </PanelBody>
          )}

          <PanelBody className="border-hairline flex items-center justify-between gap-2 border-t pt-4">
            <PillButton variant="neutral" onClick={() => setStep("describe")}>
              <ArrowLeft className="size-5" />
              {t("newSource.back")}
            </PillButton>
            <PillButton variant="next" onClick={createSource} disabled={busy}>
              {busy ? t("newSource.creating") : t("newSource.createSource")}
              <ArrowRight className="size-5" />
            </PillButton>
          </PanelBody>
        </PanelCard>
      )}

      {step === "zones" && cameraId && (
        <PanelCard>
          <PanelBody>
            <span className="text-[20px] leading-tight font-semibold">
              {t("newSource.drawZones")}
            </span>
          </PanelBody>
          <PanelBody>
            <ZoneEditor cameraId={cameraId} />
          </PanelBody>
          <PanelBody className="border-hairline flex items-center justify-between gap-2 border-t pt-4">
            <PillButton variant="neutral" onClick={() => setStep("describe")}>
              <ArrowLeft className="size-5" />
              {t("newSource.back")}
            </PillButton>
            <PillButton variant="next" onClick={() => setStep("run")}>
              {t("newSource.continue")}
              <ArrowRight className="size-5" />
            </PillButton>
          </PanelBody>
        </PanelCard>
      )}

      {step === "run" && cameraId && !status && (
        <PanelCard className="min-h-[140px] justify-between">
          <PanelBody className="flex flex-col gap-2">
            <span className="text-base font-semibold">
              {sourceType === "upload"
                ? t("newSource.analyzeVideo")
                : t("newSource.captureAndAnalyze")}
            </span>
            <span className="text-ink-muted text-xs leading-4 font-medium">
              {sourceType === "upload" ? t("newSource.uploadBlurb") : t("newSource.cctvRunBlurb")}
            </span>
          </PanelBody>
          <PanelBody className="flex flex-wrap items-center gap-2">
            {sourceType === "cctv" && (
              <span className="relative inline-flex">
                <select
                  aria-label={t("sources.captureDuration")}
                  className={cn(FIELD, "w-[170px] appearance-none pr-9")}
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                >
                  <option value={60}>{t("newSource.capture60")}</option>
                  <option value={120}>{t("newSource.capture120")}</option>
                  <option value={300}>{t("newSource.capture300")}</option>
                </select>
                <ChevronDown
                  aria-hidden
                  className="pointer-events-none absolute top-1/2 right-[11px] size-5 -translate-y-1/2"
                />
              </span>
            )}
            <PillButton variant="dark" onClick={startRun} disabled={busy}>
              {sourceType === "upload" ? t("newSource.startAnalysis") : t("sources.captureAnalyze")}
              <ArrowRight className="size-5" />
            </PillButton>
          </PanelBody>
        </PanelCard>
      )}

      {step === "run" &&
        cameraId &&
        status &&
        status.state !== "done" &&
        status.state !== "error" && (
          <PanelCard>
            <PanelBody className="flex flex-col gap-2">
              <span className="text-base font-semibold">
                {status.state === "capturing"
                  ? t("newSource.capturingSegment")
                  : t("newSource.analyzingVideo")}
              </span>
              <span className="text-ink-muted text-xs leading-4 font-medium">
                {t("newSource.progressHint")}
              </span>
            </PanelBody>

            <PanelBody className="flex items-end justify-between gap-4">
              <span className="text-[36px] leading-[34px] font-semibold tabular-nums">
                {Math.round((status.progress ?? 0) * 100)}%
              </span>
              <span className="text-ink-muted text-xs leading-4 font-medium">
                {t("newSource.eventsSoFar", { count: status.events_written })}
              </span>
            </PanelBody>

            <PanelBody>
              <div className="bg-chip h-1.5 w-full overflow-hidden rounded-full">
                <div
                  className="bg-brand h-full rounded-full transition-all duration-500"
                  style={{ width: `${Math.round((status.progress ?? 0) * 100)}%` }}
                />
              </div>
            </PanelBody>

            <PanelBody className="flex flex-wrap items-center gap-4 text-xs font-medium">
              {stagesFor(t, sourceType).map((label, i) => {
                const active = i === stageIndex(t, sourceType, status.state);
                const done = i < stageIndex(t, sourceType, status.state);
                return (
                  <span
                    key={label}
                    className={cn(
                      "inline-flex items-center gap-1.5",
                      active ? "text-foreground" : done ? "text-ink-muted" : "text-ink-faint",
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        "size-1.5 rounded-full",
                        active ? "bg-brand animate-pulse" : done ? "bg-brand" : "bg-chip",
                      )}
                    />
                    {label}
                  </span>
                );
              })}
            </PanelBody>
          </PanelCard>
        )}

      {step === "run" && cameraId && status?.state === "error" && (
        <PanelCard className="border-danger-line bg-danger-line/5">
          <PanelBody className="flex flex-col gap-2">
            <span className="text-danger-ink text-base font-semibold">{t("newSource.failed")}</span>
            <span className="text-danger-ink text-sm">
              {status.error ?? t("newSource.jobFailed")}
            </span>
          </PanelBody>
          <PanelBody>
            <PillButton variant="neutral" onClick={() => setStatus(null)}>
              {t("error.retry")}
            </PillButton>
          </PanelBody>
        </PanelCard>
      )}

      {step === "run" && cameraId && status?.state === "done" && (
        <PanelCard>
          <PanelBody className="flex flex-col gap-2">
            <span className="text-base font-semibold">
              {t("newSource.complete", { count: status.events_written })}
            </span>
            <span className="text-ink-muted text-xs leading-4 font-medium">
              {t("newSource.completeHint")}
            </span>
          </PanelBody>
          {processedUrl && (
            <PanelBody>
              <VideoPlayer
                src={processedUrl}
                title={name || t("newSource.processedVideo")}
                className="h-[280px] sm:h-[400px] lg:h-[538px]"
              />
            </PanelBody>
          )}
          <PanelBody className="flex flex-wrap gap-2">
            <Link href="/" className={pillVariants({ variant: "next" })}>
              {t("newSource.backToSources")}
              <ArrowRight className="size-5" />
            </Link>
            <Link href="/dashboard" className={pillVariants({ variant: "quiet" })}>
              {t("nav.dashboard")}
            </Link>
            <Link href="/ask" className={pillVariants({ variant: "quiet" })}>
              {t("nav.ask")}
            </Link>
          </PanelBody>
        </PanelCard>
      )}
    </div>
  );
}

function SourceTypeCard({
  title,
  description,
  icon,
  onClick,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="border-surface-border hover:border-brand/40 flex h-[140px] min-w-[280px] flex-1 flex-col items-start justify-between rounded-[16px] border bg-white p-4 text-left transition-colors"
    >
      <span className="flex flex-col gap-2">
        <span className="text-base font-semibold">{title}</span>
        <span className="text-ink-muted text-xs leading-4 font-medium">{description}</span>
      </span>
      {icon}
    </button>
  );
}
