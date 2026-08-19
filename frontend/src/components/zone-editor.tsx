"use client";

import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { CloseCircleIcon } from "@/components/icons";
import { PillButton } from "@/components/pill-button";
import { api, apiBlob, type Zone } from "@/lib/api";

import { useT, type TFunc } from "@/lib/locale";
import { cn } from "@/lib/utils";

const KIND_COLORS: Record<Zone["kind"], string> = {
  entrance: "#16a34a",
  checkout_area: "#e87000",
  store_room: "#dc2626",
  dining: "#2563eb",
  truck: "#b45309",
  delivery_door: "#9333ea",
  custom: "#525252",
};

const kindHints = (t: TFunc): Record<Zone["kind"], string> => ({
  entrance: t("newSource.hint.entrance"),
  checkout_area: t("newSource.hint.checkout_area"),
  store_room: t("newSource.hint.store_room"),
  dining: t("newSource.hint.dining"),
  truck: t("newSource.hint.truck"),
  delivery_door: t("newSource.hint.delivery_door"),
  custom: t("newSource.hint.custom"),
});

const KINDS: Zone["kind"][] = [
  "entrance",
  "checkout_area",
  "store_room",
  "dining",
  "truck",
  "delivery_door",
  "custom",
];

const CONTROL =
  "h-[38px] rounded-[8px] border border-hairline bg-white px-[11px] text-sm font-medium text-foreground outline-none placeholder:text-ink-muted focus-visible:ring-2 focus-visible:ring-brand/40";

function Checkbox({
  checked,
  onChange,
  label,
  title,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  title?: string;
}) {
  return (
    <label className="flex items-center gap-3 text-sm font-medium" title={title}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="checked:border-brand-deep checked:bg-brand-deep size-[18px] shrink-0 appearance-none rounded-[4px] border-[1.5px] border-[#999ea3] bg-white"
      />
      {label}
    </label>
  );
}

export function ZoneEditor({
  cameraId,
  onZonesChange,
}: {
  cameraId: string;
  onZonesChange?: (count: number) => void;
}) {
  const t = useT();
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [points, setPoints] = useState<number[][]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Zone["kind"]>("entrance");
  const [recordClips, setRecordClips] = useState(false);
  const [privacyMask, setPrivacyMask] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const [cursor, setCursor] = useState<[number, number]>([50, 50]);
  const [focused, setFocused] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const hintId = useId();

  const loadZones = useCallback(async () => {
    const all = await api<Zone[]>("/api/zones");
    const mine = all.filter((z) => z.camera_id === cameraId);
    setZones(mine);
    onZonesChange?.(mine.length);
  }, [cameraId, onZonesChange]);

  useEffect(() => {
    apiBlob(`/api/cameras/${cameraId}/snapshot`)
      .then(setSnapshot)
      .catch(() => setSnapshot(null));
    loadZones();
  }, [cameraId, loadZones]);

  useEffect(() => {
    setRecordClips(kind === "store_room");
    if (!name || (KINDS as string[]).includes(name)) setName(kind);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  function handleClick(e: React.MouseEvent<SVGSVGElement>) {
    const rect = svgRef.current!.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setPoints((prev) => [...prev, [Number(x.toFixed(4)), Number(y.toFixed(4))]]);
  }

  function handleKeyDown(e: React.KeyboardEvent<SVGSVGElement>) {
    const step = e.shiftKey ? 5 : 1;
    const move = (dx: number, dy: number) => {
      e.preventDefault();
      setCursor(([cx, cy]) => [
        Math.min(100, Math.max(0, cx + dx * step)),
        Math.min(100, Math.max(0, cy + dy * step)),
      ]);
    };
    switch (e.key) {
      case "ArrowLeft":
        return move(-1, 0);
      case "ArrowRight":
        return move(1, 0);
      case "ArrowUp":
        return move(0, -1);
      case "ArrowDown":
        return move(0, 1);
      case "Enter":
      case " ":
        e.preventDefault();
        return setPoints((prev) => [
          ...prev,
          [Number((cursor[0] / 100).toFixed(4)), Number((cursor[1] / 100).toFixed(4))],
        ]);
      case "Backspace":
        e.preventDefault();
        return setPoints((prev) => prev.slice(0, -1));
      case "Escape":
        e.preventDefault();
        return setPoints([]);
    }
  }

  async function addZone() {
    if (points.length < 3 || !name.trim()) {
      setStatus(t("zones.needPoints"));
      return;
    }
    try {
      await api("/api/zones", {
        method: "POST",
        body: JSON.stringify({
          camera_id: cameraId,
          name: name.trim(),
          kind,
          polygon: points,
          record_clips: recordClips,
          privacy_mask: privacyMask,
        }),
      });
      setPoints([]);
      setStatus(null);
      loadZones();
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function removeZone(zone: Zone) {
    await api(`/api/zones/${zone.id}`, { method: "DELETE" });
    loadZones();
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="relative w-full overflow-hidden rounded-[6px] bg-black">
        {snapshot ? (
          <img src={snapshot} alt={t("zones.firstFrame")} className="block w-full select-none" />
        ) : (
          <div className="flex h-64 items-center justify-center text-sm text-neutral-400">
            {t("zones.loadingFrame")}
          </div>
        )}
        <svg
          ref={svgRef}
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="focus-visible:ring-ring absolute inset-0 h-full w-full cursor-crosshair focus-visible:ring-2 focus-visible:outline-none"
          onClick={handleClick}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          tabIndex={0}
          role="application"
          aria-label={t("zones.canvasLabel")}
          aria-describedby={hintId}
        >
          {zones.map((z) => (
            <g key={z.id}>
              <polygon
                points={z.polygon.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")}
                fill={KIND_COLORS[z.kind]}
                fillOpacity={0.18}
                stroke={KIND_COLORS[z.kind]}
                strokeWidth={0.4}
              />
              <text
                x={z.polygon[0][0] * 100}
                y={Math.max(2.5, z.polygon[0][1] * 100 - 1)}
                fontSize={3}
                fill={KIND_COLORS[z.kind]}
              >
                {z.name}
              </text>
            </g>
          ))}
          {points.length > 0 && (
            <polygon
              points={points.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")}
              fill={KIND_COLORS[kind]}
              fillOpacity={0.25}
              stroke={KIND_COLORS[kind]}
              strokeWidth={0.5}
              strokeDasharray="1.5 1"
            />
          )}
          {points.map(([x, y], i) => (
            <circle key={i} cx={x * 100} cy={y * 100} r={0.8} fill={KIND_COLORS[kind]} />
          ))}

          {focused && (
            <g pointerEvents="none">
              <line
                x1={cursor[0]}
                y1={0}
                x2={cursor[0]}
                y2={100}
                stroke="white"
                strokeWidth={0.2}
              />
              <line
                x1={0}
                y1={cursor[1]}
                x2={100}
                y2={cursor[1]}
                stroke="white"
                strokeWidth={0.2}
              />
            </g>
          )}
        </svg>
      </div>

      <p id={hintId} className="text-ink-muted text-xs">
        {t("zones.canvasHint")}
      </p>

      <p aria-live="polite" className="sr-only">
        {focused
          ? t("zones.cursorAt", {
              x: Math.round(cursor[0]),
              y: Math.round(cursor[1]),
              count: points.length,
            })
          : ""}
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <span className="relative inline-flex">
          <select
            aria-label={t("newSource.zoneKind")}
            value={kind}
            onChange={(e) => setKind(e.target.value as Zone["kind"])}
            className={cn(CONTROL, "w-[200px] appearance-none pr-9")}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <ChevronDown
            aria-hidden
            className="pointer-events-none absolute top-1/2 right-[11px] size-5 -translate-y-1/2"
          />
        </span>
        <input
          aria-label={t("newSource.zoneName")}
          placeholder={t("newSource.zoneName")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className={cn(CONTROL, "w-[200px]")}
        />
        <div className="flex flex-1 flex-wrap items-center gap-4 px-2">
          <Checkbox
            checked={recordClips}
            onChange={setRecordClips}
            label={t("zones.recordClips")}
          />
          <Checkbox
            checked={privacyMask}
            onChange={setPrivacyMask}
            label={t("zones.privacyBlur")}
            title={t("zones.privacyBlurHint")}
          />
        </div>
        <PillButton
          variant="dark"
          onClick={addZone}
          disabled={points.length < 3 || !name.trim()}
          className="min-w-[100px]"
        >
          {t("zones.addZone")}
        </PillButton>
        <PillButton variant="quiet" onClick={() => setPoints([])} disabled={points.length === 0}>
          {t("zones.clearPoints")}
        </PillButton>
      </div>

      <p className="text-ink-muted text-sm font-medium">
        Click on the frame to add polygon points ({points.length} so far) · {kindHints(t)[kind]}
      </p>

      {status && (
        <p role="alert" aria-live="polite" className="text-danger-ink text-sm">
          {status}
        </p>
      )}

      {zones.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {zones.map((z) => (
            <span
              key={z.id}
              className="border-hairline inline-flex items-center gap-1.5 rounded-[8px] border bg-white px-[11px] py-1.5 text-sm font-medium"
            >
              <span
                aria-hidden
                className="size-[10px] shrink-0 rounded-full"
                style={{ background: KIND_COLORS[z.kind] }}
              />
              {z.kind}
              <span className="border-surface-border inline-flex h-7 items-center rounded-[20px] border bg-white px-2 text-xs">
                {z.name}
              </span>
              {z.privacy_mask && (
                <span className="bg-chip text-ink-muted inline-flex h-7 items-center rounded-[20px] px-2 text-xs">
                  blurred
                </span>
              )}
              <button
                type="button"
                onClick={() => removeZone(z)}
                aria-label={`delete ${z.name}`}
                className="text-ink-muted hover:text-danger-ink transition-colors"
              >
                <CloseCircleIcon className="size-6" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
