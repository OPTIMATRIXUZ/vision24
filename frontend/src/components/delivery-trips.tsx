"use client";

import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/error-note";
import { Lightbox, type LightboxState } from "@/components/lightbox";
import { getProducts, saveTripAsSample, type DeliveryTrip, type ProductType } from "@/lib/api";

import { useT } from "@/lib/locale";

function timeRange(trip: DeliveryTrip): string {
  const from = new Date(trip.ts_start).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  if (!trip.ts_end) return from;
  const to = new Date(trip.ts_end).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return `${from}–${to}`;
}

export function DeliveryTrips({ trips }: { trips: DeliveryTrip[] }) {
  const t = useT();
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);
  const [products, setProducts] = useState<ProductType[]>([]);
  const [savedTo, setSavedTo] = useState<Record<number, string>>({});
  const [error, setError] = useState<unknown>(null);
  const hasCrops = trips.some((trip) => trip.crop_url);

  useEffect(() => {
    if (!hasCrops) return;
    getProducts().then(setProducts).catch(setError);
  }, [hasCrops]);

  if (trips.length === 0) {
    return <p className="text-ink-muted text-xs">{t("deliveries.empty")}</p>;
  }

  async function saveSample(trip: DeliveryTrip, productId: string) {
    setError(null);
    try {
      await saveTripAsSample(trip.event_id, productId);
      const product = products.find((p) => p.id === productId);
      setSavedTo((prev) => ({ ...prev, [trip.event_id]: product?.name ?? "✓" }));
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {lightbox && (
        <Lightbox url={lightbox.url} caption={lightbox.caption} onClose={() => setLightbox(null)} />
      )}
      <ErrorNote error={error} />
      {trips.map((trip, i) => {
        const caption = `${t("deliveries.trip")} ${i + 1} · ${timeRange(trip)}`;
        const itemsText = trip.items
          .map((item) => `${item.count}× ${item.product_name}`)
          .join(", ");
        return (
          <div
            key={trip.event_id}
            className="border-hairline flex items-center gap-3 rounded-[6px] border p-2"
          >
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium">
                {t("deliveries.trip")} {i + 1}
                <span className="text-ink-muted font-normal"> · {timeRange(trip)}</span>
              </div>
              <div className="truncate text-sm">
                {itemsText || t("deliveries.unrecognizedOnly")}
                {trip.unmatched > 0 && (
                  <span className="text-ink-muted text-xs">
                    {" "}
                    (+{trip.unmatched} {t("deliveries.unmatchedShort")})
                  </span>
                )}
              </div>
              {trip.items.length > 0 && (
                <div className="text-ink-faint text-[10px] leading-4">
                  {trip.items
                    .map((item) => `${item.product_name}: ${Math.round(item.confidence * 100)}%`)
                    .join(" · ")}
                </div>
              )}
              {trip.crop_url && products.length > 0 && (
                <div className="mt-1">
                  {savedTo[trip.event_id] ? (
                    <span role="status" className="text-ink-muted text-[11px]">
                      {t("deliveries.sampleSaved", { name: savedTo[trip.event_id] })}
                    </span>
                  ) : (
                    <select
                      aria-label={t("deliveries.saveSample")}
                      defaultValue=""
                      onChange={(e) => {
                        if (e.target.value) saveSample(trip, e.target.value);
                      }}
                      className="border-hairline text-ink-muted h-6 rounded-[6px] border bg-white px-1 text-[11px]"
                    >
                      <option value="" disabled>
                        {t("deliveries.saveSample")}
                      </option>
                      {products.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </div>
            {trip.crop_url && (
              <button
                type="button"
                title={t("deliveries.cropTitle")}
                onClick={() => setLightbox({ url: trip.crop_url!, caption })}
                className="shrink-0 cursor-zoom-in"
              >
                <img
                  src={trip.crop_url}
                  alt={t("deliveries.cropTitle")}
                  loading="lazy"
                  className="border-hairline h-[44px] w-[44px] rounded-[4px] border object-cover"
                />
              </button>
            )}
            {trip.snapshot_url && (
              <button
                type="button"
                title={caption}
                onClick={() => setLightbox({ url: trip.snapshot_url!, caption })}
                className="shrink-0 cursor-zoom-in"
              >
                <img
                  src={trip.snapshot_url}
                  alt={caption}
                  loading="lazy"
                  className="border-hairline h-[44px] w-[110px] rounded-[4px] border object-cover"
                />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
