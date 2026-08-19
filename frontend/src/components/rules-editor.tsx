"use client";

import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/error-note";
import { Button } from "@/components/ui/button";
import {
  createAlertRule,
  deleteAlertRule,
  getAlertRules,
  updateAlertRule,
  api,
  type AlertRule,
  type Zone,
} from "@/lib/api";

import { useT } from "@/lib/locale";

export function RulesEditor({ cameraId }: { cameraId: string }) {
  const t = useT();
  const [zones, setZones] = useState<Zone[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [newZone, setNewZone] = useState("");
  const [newMetric, setNewMetric] = useState<"queue_len" | "occupancy">("occupancy");
  const [newThreshold, setNewThreshold] = useState(3);
  const [newSustain, setNewSustain] = useState(15);

  async function refresh() {
    try {
      const [allZones, allRules] = await Promise.all([api<Zone[]>("/api/zones"), getAlertRules()]);
      const mine = allZones.filter((z) => z.camera_id === cameraId);
      setZones(mine);
      const zoneIds = new Set(mine.map((z) => z.id));
      setRules(allRules.filter((r) => zoneIds.has(r.zone_id)));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraId]);

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e);
    }
  }

  const zoneName = (id: string) => zones.find((z) => z.id === id)?.name ?? "?";

  return (
    <div className="flex flex-col gap-2 rounded-md border p-3">
      <p className="text-sm font-medium">{t("rules.title")}</p>
      <ErrorNote error={error} />
      {rules.length === 0 && <p className="text-xs text-neutral-500">{t("rules.empty")}</p>}
      {rules.map((r) => (
        <div key={r.id} className="flex flex-wrap items-center gap-2 text-xs">
          <span className="w-24 truncate font-medium">{zoneName(r.zone_id)}</span>
          <span className="text-neutral-500">{r.metric}</span>
          <label className="flex items-center gap-1">
            ≥
            <input
              type="number"
              min={1}
              defaultValue={r.threshold}
              className="w-14 rounded border px-1 py-0.5"
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (v !== r.threshold && v >= 1)
                  act(() => updateAlertRule(r.id, { ...r, threshold: v }));
              }}
            />
          </label>
          <label className="flex items-center gap-1">
            {t("rules.for")}
            <input
              type="number"
              min={1}
              defaultValue={r.sustain_seconds}
              className="w-14 rounded border px-1 py-0.5"
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (v !== r.sustain_seconds && v >= 1)
                  act(() => updateAlertRule(r.id, { ...r, sustain_seconds: v }));
              }}
            />
            s
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={r.is_active}
              onChange={(e) =>
                act(() => updateAlertRule(r.id, { ...r, is_active: e.target.checked }))
              }
            />
            {t("rules.active")}
          </label>
          <button
            className="focus-visible:ring-ring rounded-sm text-red-600 hover:underline focus-visible:ring-2 focus-visible:outline-none"
            aria-label={t("rules.deleteRule", { zone: zoneName(r.zone_id) })}
            onClick={() => act(() => deleteAlertRule(r.id))}
          >
            {t("rules.delete")}
          </button>
        </div>
      ))}

      {zones.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t pt-2 text-xs">
          <select
            className="focus-visible:ring-ring rounded border bg-white px-1 py-0.5 focus-visible:ring-2 focus-visible:outline-none"
            aria-label={t("rules.zone")}
            value={newZone}
            onChange={(e) => setNewZone(e.target.value)}
          >
            <option value="">{t("rules.zonePlaceholder")}</option>
            {zones.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name}
              </option>
            ))}
          </select>
          <select
            className="focus-visible:ring-ring rounded border bg-white px-1 py-0.5 focus-visible:ring-2 focus-visible:outline-none"
            aria-label={t("rules.metric")}
            value={newMetric}
            onChange={(e) => setNewMetric(e.target.value as "queue_len" | "occupancy")}
          >
            <option value="occupancy">occupancy</option>
            <option value="queue_len">queue_len</option>
          </select>
          <label className="flex items-center gap-1">
            ≥
            <input
              type="number"
              min={1}
              value={newThreshold}
              onChange={(e) => setNewThreshold(Number(e.target.value))}
              className="w-14 rounded border px-1 py-0.5"
            />
          </label>
          <label className="flex items-center gap-1">
            {t("rules.for")}
            <input
              type="number"
              min={1}
              value={newSustain}
              onChange={(e) => setNewSustain(Number(e.target.value))}
              className="w-14 rounded border px-1 py-0.5"
            />
            s
          </label>
          <Button
            size="sm"
            variant="outline"
            disabled={!newZone}
            onClick={() =>
              act(() =>
                createAlertRule({
                  zone_id: newZone,
                  metric: newMetric,
                  threshold: newThreshold,
                  sustain_seconds: newSustain,
                  is_active: true,
                }),
              )
            }
          >
            {t("rules.addRule")}
          </Button>
        </div>
      )}
      <p className="text-[11px] text-neutral-400">{t("rules.applyNote")}</p>
    </div>
  );
}
