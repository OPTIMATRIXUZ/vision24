"use client";

import { useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ErrorNote } from "@/components/error-note";
import { CloseCircleIcon } from "@/components/icons";
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
  addProductSample,
  createProduct,
  deleteProduct,
  deleteProductSample,
  getProducts,
  type ProductType,
} from "@/lib/api";

import { useT } from "@/lib/locale";

const MAX_SAMPLES = 5;

export function ProductCatalog() {
  const t = useT();
  const [products, setProducts] = useState<ProductType[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [units, setUnits] = useState("");
  const [unitLabel, setUnitLabel] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ProductType | null>(null);
  const uploadTarget = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function refresh() {
    try {
      setProducts(await getProducts());
      setError(null);
    } catch (e) {
      setError(e);
    }
  }

  useEffect(() => {
    getProducts().then(setProducts).catch(setError);
  }, []);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const parsedUnits = units.trim() ? Number(units) : null;
    await act(() =>
      createProduct({
        name: name.trim(),
        units_per_package: parsedUnits && parsedUnits > 0 ? Math.round(parsedUnits) : null,
        unit_label: unitLabel.trim() || null,
      }),
    );
    setName("");
    setUnits("");
    setUnitLabel("");
  }

  function pickSample(productId: string) {
    uploadTarget.current = productId;
    fileInput.current?.click();
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    const productId = uploadTarget.current;
    e.target.value = "";
    if (!file || !productId) return;
    await act(() => addProductSample(productId, file));
  }

  return (
    <PanelCard className="max-w-2xl">
      <input
        ref={fileInput}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        onChange={onFile}
        className="hidden"
      />
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => !next && setDeleteTarget(null)}
        title={t("products.deleteTitle", { name: deleteTarget?.name ?? "" })}
        description={t("products.deleteBody")}
        confirmLabel={t("products.delete")}
        onConfirm={async () => {
          if (deleteTarget) await act(() => deleteProduct(deleteTarget.id));
        }}
      />

      <PanelHeader>
        <PanelTitleRow>
          <PanelTitle>{t("products.title")}</PanelTitle>
        </PanelTitleRow>
        <PanelSubtitle>{t("products.subtitle")}</PanelSubtitle>
      </PanelHeader>

      <PanelBody className="flex flex-col gap-4">
        <ErrorNote error={error} />

        {products?.length === 0 && <p className="text-ink-muted text-xs">{t("products.empty")}</p>}

        {products?.map((p) => (
          <div key={p.id} className="border-hairline flex flex-col gap-2 rounded-[8px] border p-3">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{p.name}</span>
              {p.units_per_package != null && (
                <span className="text-ink-muted text-xs">
                  {p.units_per_package} {p.unit_label || t("deliveries.units")}
                </span>
              )}
              <PillButton
                variant="danger"
                className="h-7 px-2 text-xs"
                onClick={() => setDeleteTarget(p)}
                disabled={busy}
              >
                {t("products.delete")}
              </PillButton>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {p.samples.map((s) => (
                <span key={s.id} className="relative inline-block">
                  <img
                    src={s.url}
                    alt={p.name}
                    loading="lazy"
                    className="border-hairline h-[62px] w-[62px] rounded-[6px] border object-cover"
                  />
                  <button
                    type="button"
                    title={t("products.deleteSample")}
                    onClick={() => act(() => deleteProductSample(p.id, s.id))}
                    disabled={busy}
                    className="absolute -top-1.5 -right-1.5 rounded-full bg-white shadow"
                  >
                    <CloseCircleIcon className="size-4" />
                  </button>
                </span>
              ))}
              {p.samples.length < MAX_SAMPLES && (
                <button
                  type="button"
                  onClick={() => pickSample(p.id)}
                  disabled={busy}
                  className="border-hairline text-ink-muted hover:text-foreground flex h-[62px] w-[62px] items-center justify-center rounded-[6px] border border-dashed text-2xl leading-none"
                  title={t("products.addSample")}
                >
                  +
                </button>
              )}
            </div>
            {p.samples.length === 0 && (
              <p className="text-ink-faint text-[11px] leading-4">{t("products.needsPhotos")}</p>
            )}
          </div>
        ))}

        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1">
              <label htmlFor="product-name" className="text-xs font-medium">
                {t("products.name")}
              </label>
              <Input
                id="product-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("products.namePlaceholder")}
                className="w-48"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="product-units" className="text-xs font-medium">
                {t("products.unitsPerPackage")}
              </label>
              <Input
                id="product-units"
                type="number"
                min={1}
                value={units}
                onChange={(e) => setUnits(e.target.value)}
                placeholder="24"
                className="w-24"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="product-unit-label" className="text-xs font-medium">
                {t("products.unitLabel")}
              </label>
              <Input
                id="product-unit-label"
                value={unitLabel}
                onChange={(e) => setUnitLabel(e.target.value)}
                placeholder={t("products.unitLabelPlaceholder")}
                className="w-32"
              />
            </div>
            <PillButton variant="primary" onClick={add} disabled={busy || !name.trim()}>
              {t("products.add")}
            </PillButton>
          </div>
          <p className="text-ink-muted text-xs leading-4">{t("products.samplesHint")}</p>
          <p className="text-ink-muted text-xs leading-4">{t("products.zonesNote")}</p>
        </div>
      </PanelBody>
    </PanelCard>
  );
}
