"use client";

import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/error-note";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ChatPanel } from "@/components/chat-panel";

import { useT, type TFunc } from "@/lib/locale";
import { useResource } from "@/lib/use-resource";
import {
  api,
  getSelectedSite,
  GO2RTC_URL,
  replayStatus,
  startReplay,
  workerStatus,
  type Camera,
  type LiveMetrics,
  type WorkerStatus,
} from "@/lib/api";

function streamOf(camera: Camera): string {
  const name = camera.rtsp_url.replace(/\/+$/, "").split("/").pop() ?? "";
  return /^[A-Za-z0-9_-]{1,32}$/.test(name) ? name : "cam1";
}

const METRICS_POLL_MS = 1500;

const examples = (t: TFunc) => [t("live.q1"), t("live.q2"), t("live.q3"), t("live.q4")];

function WorkerBadge({ worker }: { worker: WorkerStatus | null }) {
  const t = useT();
  if (!worker) return null;
  if (!worker.running) {
    return (
      <Badge
        variant="outline"
        className="border-neutral-300 bg-neutral-50 text-neutral-600"
        title={t("live.startWorker")}
      >
        {t("live.detectionOff")}
      </Badge>
    );
  }
  const live = worker.cameras.filter((c) => c.state === "running");
  const healthy = live.length === worker.cameras.length && live.length > 0;
  return (
    <Badge
      variant="outline"
      className={
        healthy
          ? "border-emerald-300 bg-emerald-50 text-emerald-700"
          : "border-amber-300 bg-amber-50 text-amber-700"
      }
      title={worker.cameras.map((c) => `${c.name}: ${c.state}`).join("\n")}
    >
      {t("live.detectionOn", { live: live.length, total: worker.cameras.length })}
    </Badge>
  );
}

export default function LivePage() {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const site = getSelectedSite();

  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedCamera, setSelectedCamera] = useState<Camera | null>(null);
  useEffect(() => {
    api<Camera[]>("/api/cameras")
      .then((all) => {
        const live = all.filter((c) => c.role === "cctv" && c.is_active);
        setCameras(live);
        setSelectedCamera((current) => current ?? live[0] ?? null);
      })
      .catch(() => setCameras([]));
  }, [site]);
  const stream = selectedCamera ? streamOf(selectedCamera) : "cam1";

  const { data } = useResource(
    async () => {
      const [metrics, worker, replay] = await Promise.allSettled([
        api<LiveMetrics>("/api/metrics/live"),
        workerStatus(),
        replayStatus(),
      ]);
      return {
        metrics: metrics.status === "fulfilled" ? metrics.value : null,
        worker: worker.status === "fulfilled" ? worker.value : null,
        playing: replay.status === "fulfilled" ? replay.value.playing : false,
      };
    },
    { pollMs: METRICS_POLL_MS, deps: [site] },
  );

  const metrics = data?.metrics ?? null;
  const worker = data?.worker ?? null;
  const [replayOverride, setReplayOverride] = useState<boolean | null>(null);
  const playing = replayOverride ?? data?.playing ?? false;

  async function handleReplay() {
    setBusy(true);
    setError(null);
    try {
      const r = await startReplay(selectedCamera?.id);
      setReplayOverride(r.playing);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <h1 className="text-base font-semibold">{t("live.title")}</h1>
        <WorkerBadge worker={worker} />
        {metrics && (
          <Badge variant="secondary">
            {t("live.onScreen", { count: metrics.total_occupancy })}
          </Badge>
        )}
        {metrics?.queues.map((q) => (
          <Badge
            key={q.zone_id}
            variant="outline"
            className={
              q.threshold !== null && q.queue_len >= q.threshold
                ? "border-red-300 bg-red-50 text-red-700"
                : ""
            }
          >
            {t("live.queue", { zone: q.name, count: q.queue_len })}
          </Badge>
        ))}
        <Button
          size="sm"
          variant={playing ? "outline" : "default"}
          onClick={handleReplay}
          disabled={busy}
          className="ml-auto"
        >
          {playing ? t("live.replay") : t("live.watch")}
        </Button>
      </div>

      <ErrorNote error={error} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardContent className="p-2">
            {cameras.length > 1 && (
              <div className="flex flex-wrap gap-1.5 px-2 pb-2">
                {cameras.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => setSelectedCamera(c)}
                    aria-pressed={c.id === selectedCamera?.id}
                    className={
                      c.id === selectedCamera?.id
                        ? "bg-foreground text-background h-6 rounded-[20px] px-2 text-xs font-medium"
                        : "bg-chip text-ink-muted hover:text-foreground h-6 rounded-[20px] px-2 text-xs font-medium transition-colors"
                    }
                  >
                    {c.name}
                  </button>
                ))}
              </div>
            )}
            <iframe
              key={stream}
              src={`${GO2RTC_URL}/stream.html?src=${stream}`}
              className="aspect-video w-full rounded-md border-0 bg-black"
              allow="autoplay"
              title={t("live.feedTitle")}
            />
            <p className="px-2 pt-1 text-[11px] text-neutral-400">{t("live.privacyNote")}</p>
          </CardContent>
        </Card>

        <ChatPanel
          className="h-[68vh]"
          compact
          surface="live"
          title={t("live.chatTitle")}
          placeholder={t("live.chatPlaceholder")}
          examples={examples(t)}
          emptyHint={t("live.chatHint")}
        />
      </div>
    </div>
  );
}
