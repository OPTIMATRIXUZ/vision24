"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { DeliveriesCard } from "@/components/dashboard/deliveries-card";
import { EntriesChart } from "@/components/dashboard/entries-chart";
import { SavingsCard } from "@/components/dashboard/savings-card";
import {
  Chip,
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import { StatCard } from "@/components/dashboard/stat-card";
import { Lightbox, type LightboxState } from "@/components/lightbox";
import { Badge } from "@/components/ui/badge";
import { VideoPlayer } from "@/components/video-player";
import {
  api,
  getDeliveries,
  getHeatmap,
  getPosDiscrepancies,
  getProcessedVideo,
  getSavings,
  getSources,
  type Alert,
  type DeliverySummary,
  type EntryFrame,
  type LiveMetrics,
  type PosDiscrepancies,
  type Savings,
  type Summary,
  type TrafficBucket,
} from "@/lib/api";
import { cn } from "@/lib/utils";

import { useT } from "@/lib/locale";

const POLL_MS = 5000;

export default function DashboardPage() {
  const t = useT();
  const [live, setLive] = useState<LiveMetrics | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [traffic, setTraffic] = useState<TrafficBucket[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [entries, setEntries] = useState<EntryFrame[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);
  const [processedVideos, setProcessedVideos] = useState<{ name: string; url: string }[]>([]);
  const [selectedVideo, setSelectedVideo] = useState(0);
  const [heatmaps, setHeatmaps] = useState<{ name: string; url: string }[]>([]);
  const [deliveries, setDeliveries] = useState<DeliverySummary | null>(null);
  const [savings, setSavings] = useState<Savings | null>(null);
  const [pos, setPos] = useState<PosDiscrepancies | null>(null);

  useEffect(() => {
    getSources()
      .then((sources) => {
        Promise.all(
          sources
            .filter((s) => s.has_processed)
            .map((s) =>
              getProcessedVideo(s.camera_id)
                .then((r) => ({ name: s.name, url: r.url }))
                .catch(() => null),
            ),
        ).then((videos) => setProcessedVideos(videos.filter((v) => v !== null)));
        Promise.all(
          sources.map((s) =>
            getHeatmap(s.camera_id)
              .then((r) => ({ name: s.name, url: r.url }))
              .catch(() => null),
          ),
        ).then((maps) => setHeatmaps(maps.filter((m) => m !== null)));
      })
      .catch(() => {
        setProcessedVideos([]);
        setHeatmaps([]);
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [l, s, t, a, e, d, sv, p] = await Promise.all([
          api<LiveMetrics>("/api/metrics/live"),
          api<Summary>("/api/metrics/summary"),
          api<TrafficBucket[]>("/api/metrics/traffic"),
          api<Alert[]>("/api/alerts?limit=10"),
          api<EntryFrame[]>("/api/metrics/entries"),
          getDeliveries().catch(() => null),
          getSavings().catch(() => null),
          getPosDiscrepancies().catch(() => null),
        ]);
        if (cancelled) return;
        setLive(l);
        setSummary(s);
        setTraffic(t);
        setAlerts(a);
        setEntries(e);
        setDeliveries(d);
        setSavings(sv);
        setPos(p);
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
  }, []);

  const chartData = traffic.map((b) => ({
    hour: new Date(b.bucket_start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    entries: b.entries,
  }));

  const openFrame = (url: string, caption?: string) => setLightbox({ url, caption });

  const peakMeta =
    [
      summary?.peak_occupancy.ts ? new Date(summary.peak_occupancy.ts).toLocaleTimeString() : null,
      summary?.peak_occupancy.camera_name ?? null,
    ]
      .filter(Boolean)
      .join(" · ") || undefined;

  const topDwell = summary?.avg_dwell[0];

  const topDwellSnapshot =
    live?.per_zone.find((z) => z.name === topDwell?.zone_name)?.snapshot_url ?? undefined;

  const activeVideo = processedVideos[Math.min(selectedVideo, processedVideos.length - 1)];

  return (
    <div className="flex flex-col gap-2">
      {lightbox && (
        <Lightbox url={lightbox.url} caption={lightbox.caption} onClose={() => setLightbox(null)} />
      )}
      {error && (
        <PanelCard className="border-destructive/30 bg-destructive/5">
          <PanelBody className="text-destructive text-sm">API error: {error}</PanelBody>
        </PanelCard>
      )}

      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl leading-7 font-semibold">{t("nav.dashboard")}</h1>
        <Link
          href="/report"
          className="border-surface-border text-foreground inline-flex h-[38px] shrink-0 items-center gap-1.5 rounded-[20px] border bg-white px-[11px] text-sm font-medium transition-colors hover:bg-neutral-50"
        >
          {t("nav.report")}
          <ArrowRight className="size-5" />
        </Link>
      </div>

      <div className="flex flex-wrap gap-2">
        <StatCard
          label={t("dashboard.peopleInView")}
          value={live ? live.total_occupancy.toLocaleString() : "—"}
          suffix="all sources"
          imageUrl={live?.snapshot_url}
          onOpen={openFrame}
        />
        <StatCard
          label={t("dashboard.entriesToday")}
          value={summary ? summary.entries_total.toLocaleString() : "—"}
          suffix={
            summary && summary.unique_visitors > 0
              ? t("dashboard.visitors", { count: summary.unique_visitors })
              : t("dashboard.today")
          }
          imageUrl={summary?.last_entry_snapshot_url}
          onOpen={openFrame}
        />
        <StatCard
          label={t("dashboard.peakOccupancy")}
          value={summary ? summary.peak_occupancy.value.toLocaleString() : "—"}
          meta={peakMeta}
          suffix="busiest camera"
          imageUrl={summary?.peak_occupancy.snapshot_url}
          onOpen={openFrame}
        />
        <StatCard
          label={t("dashboard.avgDwell")}
          value={topDwell ? `${Math.round(topDwell.avg_dwell_s)}s` : "—"}
          suffix={topDwell?.zone_name ?? "top zone"}
          imageUrl={topDwellSnapshot}
          onOpen={openFrame}
        />
      </div>

      <div className="flex flex-col gap-2 lg:flex-row">
        <PanelCard className="flex-1 lg:h-[368px]">
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("dashboard.entriesPerHour")}</PanelTitle>
              <Chip>{t("dashboard.today")}</Chip>
            </PanelTitleRow>
            <PanelSubtitle>{t("dashboard.entriesSubtitle")}</PanelSubtitle>
          </PanelHeader>
          <PanelBody className="text-foreground h-[264px] min-h-0 lg:h-auto lg:flex-1">
            <EntriesChart data={chartData} />
          </PanelBody>
        </PanelCard>

        <div className="flex flex-col gap-2 lg:w-[244px] lg:shrink-0">
          <PanelCard>
            <PanelHeader>
              <PanelTitleRow>
                <PanelTitle>{t("dashboard.queues")}</PanelTitle>
              </PanelTitleRow>
              <PanelSubtitle>{t("dashboard.queuesSubtitle")}</PanelSubtitle>
            </PanelHeader>
            <PanelBody className="flex flex-col gap-2">
              {live?.queues.length ? (
                live.queues.map((q) => {
                  const breached = q.threshold != null && q.queue_len >= q.threshold;
                  return (
                    <div
                      key={q.zone_id}
                      className={cn(
                        "border-hairline flex items-center gap-1.5 rounded-[6px] border p-1.5",
                        breached && "border-red-300 bg-red-50",
                      )}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium">{q.name}</div>
                        <div className="text-ink-muted text-[10px] leading-4">
                          Threshold {q.threshold ?? "—"}
                        </div>
                      </div>
                      {q.snapshot_url && (
                        <FrameThumb
                          url={q.snapshot_url}
                          label={`${q.name} — worst moment`}
                          onOpen={openFrame}
                          className="h-[34px] w-[61px] rounded-[4px]"
                        />
                      )}
                      <span
                        className={cn(
                          "text-[36px] leading-[34px] font-semibold tabular-nums",
                          breached && "text-red-600",
                        )}
                      >
                        {q.queue_len}
                      </span>
                    </div>
                  );
                })
              ) : (
                <p className="text-ink-muted text-xs">{t("dashboard.noCheckoutZones")}</p>
              )}
            </PanelBody>
          </PanelCard>

          <PanelCard>
            <PanelHeader>
              <PanelTitleRow>
                <PanelTitle>{t("dashboard.occupancyByZone")}</PanelTitle>
              </PanelTitleRow>
            </PanelHeader>
            <PanelBody className="flex flex-col gap-0.5">
              {live?.per_zone.length ? (
                live.per_zone.map((z) => (
                  <div
                    key={z.zone_id}
                    className="border-hairline flex items-center gap-1 border-b p-1.5"
                  >
                    <span className="min-w-0 flex-1 truncate text-xs">{z.name}</span>
                    {z.snapshot_url && (
                      <FrameThumb
                        url={z.snapshot_url}
                        label={`${z.name} — peak`}
                        onOpen={openFrame}
                        className="h-6 w-10 rounded-[4px]"
                      />
                    )}
                    <span className="text-base leading-4 font-semibold tabular-nums">
                      {z.count}
                    </span>
                  </div>
                ))
              ) : (
                <p className="text-ink-muted text-xs">{t("dashboard.noZones")}</p>
              )}
            </PanelBody>
          </PanelCard>
        </div>
      </div>

      {savings !== null && savings.total > 0 && <SavingsCard savings={savings} />}

      {pos !== null && pos.receipts_total > 0 && (
        <PanelCard>
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("dashboard.posTitle")}</PanelTitle>
              {pos.discrepancies.length > 0 && (
                <Chip className="bg-danger-line/10 text-danger-ink">
                  {t("dashboard.posFlagged", { count: pos.discrepancies.length })}
                </Chip>
              )}
              <Link href="/pos" className="text-brand ml-auto text-sm font-medium hover:underline">
                {t("dashboard.posOpen")}
                <ArrowRight className="ml-1 inline size-4" />
              </Link>
            </PanelTitleRow>
          </PanelHeader>
          {pos.discrepancies.length > 0 && (
            <PanelBody className="flex flex-col gap-1">
              {pos.discrepancies.slice(0, 3).map((d) => (
                <div
                  key={`${d.flag}-${d.ts}`}
                  className="border-hairline flex items-center gap-2 rounded-[6px] border p-1.5 text-xs"
                >
                  <Badge variant="destructive">{t(`pos.flag.${d.flag}`)}</Badge>
                  <span className="text-ink-muted tabular-nums">
                    {new Date(d.ts).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  {d.zone_name && <span className="text-ink-muted truncate">{d.zone_name}</span>}
                </div>
              ))}
            </PanelBody>
          )}
        </PanelCard>
      )}

      {deliveries !== null && deliveries.trips.length > 0 && (
        <DeliveriesCard summary={deliveries} />
      )}

      {heatmaps.length > 0 && (
        <PanelCard>
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("dashboard.trafficHeatmap")}</PanelTitle>
              <PanelSubtitle>{t("dashboard.heatmapSubtitle")}</PanelSubtitle>
            </PanelTitleRow>
          </PanelHeader>
          <PanelBody className={cn("grid gap-4", heatmaps.length > 1 && "lg:grid-cols-2")}>
            {heatmaps.map((m) => (
              <figure key={m.url} className="flex flex-col gap-1.5">
                <button
                  type="button"
                  onClick={() => openFrame(m.url, `${m.name} — traffic heatmap`)}
                  className="cursor-zoom-in"
                >
                  <img
                    src={m.url}
                    alt={`${m.name} traffic heatmap`}
                    loading="lazy"
                    className="border-hairline max-h-[420px] w-full rounded-[6px] border object-contain"
                  />
                </button>
                <figcaption className="text-ink-muted text-xs">{m.name}</figcaption>
              </figure>
            ))}
          </PanelBody>
        </PanelCard>
      )}

      {activeVideo && (
        <PanelCard>
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("dashboard.processedVideos")}</PanelTitle>
              <PanelSubtitle>{t("dashboard.processedSubtitle")}</PanelSubtitle>
            </PanelTitleRow>
            {processedVideos.length > 1 && (
              <div className="flex flex-wrap gap-1.5">
                {processedVideos.map((v, i) => (
                  <button
                    key={v.url}
                    type="button"
                    onClick={() => setSelectedVideo(i)}
                    aria-pressed={v.url === activeVideo.url}
                    className={cn(
                      "h-6 rounded-[20px] px-2 text-xs font-medium transition-colors",
                      v.url === activeVideo.url
                        ? "bg-foreground text-background"
                        : "bg-chip text-ink-muted hover:text-foreground",
                    )}
                  >
                    {v.name}
                  </button>
                ))}
              </div>
            )}
          </PanelHeader>
          <PanelBody>
            <VideoPlayer
              key={activeVideo.url}
              src={activeVideo.url}
              title={activeVideo.name}
              className="h-[280px] sm:h-[400px] lg:h-[538px]"
            />
          </PanelBody>
        </PanelCard>
      )}

      <div className="flex flex-col gap-2 lg:flex-row">
        <PanelCard className="flex-1 lg:min-h-[300px]">
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("dashboard.entriesEveryFrame")}</PanelTitle>
              <Chip>{entries.length}</Chip>
            </PanelTitleRow>
          </PanelHeader>
          <PanelBody>
            {entries.length === 0 ? (
              <p className="text-ink-muted text-xs">{t("dashboard.noEntries")}</p>
            ) : (
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                {entries.map((e) => {
                  const caption = `${new Date(e.ts).toLocaleTimeString()} · ${e.zone_name}`;
                  return e.snapshot_url ? (
                    <button
                      key={e.event_id}
                      type="button"
                      title={caption}
                      onClick={() => openFrame(e.snapshot_url!, caption)}
                      className="cursor-zoom-in"
                    >
                      <img
                        src={e.snapshot_url}
                        alt={caption}
                        loading="lazy"
                        className="aspect-[110/62] w-full rounded-[4px] object-cover"
                      />
                    </button>
                  ) : (
                    <div
                      key={e.event_id}
                      title={caption}
                      className="text-ink-faint flex aspect-[110/62] w-full items-center justify-center rounded-[4px] bg-neutral-100 text-[10px]"
                    >
                      {t("dashboard.noFrame")}
                    </div>
                  );
                })}
              </div>
            )}
          </PanelBody>
        </PanelCard>

        <PanelCard className="flex-1 lg:min-h-[300px]">
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("dashboard.recentAlerts")}</PanelTitle>
            </PanelTitleRow>
          </PanelHeader>
          <PanelBody className="flex flex-1 flex-col gap-3">
            {alerts.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-1.5">
                <span className="relative block size-8">
                  <span className="absolute top-[31.25%] right-1/4 bottom-[31.25%] left-[29.17%]">
                    <span className="absolute inset-[-8.33%_-6.82%]">
                      <img
                        src="/icons/no-alerts.svg"
                        alt=""
                        className="block size-full max-w-none"
                      />
                    </span>
                  </span>
                </span>
                <p className="text-ink-muted text-base leading-4 font-medium">
                  {t("dashboard.noAlerts")}
                </p>
              </div>
            ) : (
              alerts.map((a) => (
                <div
                  key={a.id}
                  className="border-hairline flex flex-col gap-2 rounded-[6px] border p-3 md:flex-row md:items-start"
                >
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="destructive">alert</Badge>
                      <span className="text-ink-muted text-xs">
                        {new Date(a.triggered_at).toLocaleString()}
                      </span>
                    </div>
                    <p className="mt-1 text-sm">{a.message}</p>
                  </div>
                  {a.clip_url && (
                    <video
                      src={a.clip_url}
                      poster={a.snapshot_url ?? undefined}
                      controls
                      preload="metadata"
                      className="w-full rounded-[6px] md:w-56"
                    />
                  )}
                </div>
              ))
            )}
          </PanelBody>
        </PanelCard>
      </div>
    </div>
  );
}

function FrameThumb({
  url,
  label,
  onOpen,
  className,
}: {
  url: string;
  label: string;
  onOpen: (url: string, caption?: string) => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(url, label)}
      title={label}
      className="shrink-0 cursor-zoom-in"
    >
      <img
        src={url}
        alt={label}
        loading="lazy"
        className={cn("rounded border object-cover", className)}
      />
    </button>
  );
}
