// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { t } from "@/lib/i18n";

import { VisitsList } from "./visits-list";
import type { PosVisit } from "@/lib/api";

const noop = () => {};

const VISIT: PosVisit = {
  ts_start: "2026-08-03T10:00:00+05:00",
  ts_end: "2026-08-03T10:00:24+05:00",
  zone_name: "Касса",
  kind: "sale",
  items: [{ name: "cola", qty: 2 }],
  confidence: 0.9,
  notes: "customer pays",
  snapshot_url: null,
  receipt: {
    id: "r1",
    external_id: "SIM-0001",
    kind: "sale",
    ts: "2026-08-03T10:00:12+05:00",
    total: 30_000,
    items: [],
    zone_id: null,
    zone_name: null,
    source: "simulated",
    flag: null,
  },
};

describe("VisitsList", () => {
  it("shows item chips and the linked receipt", () => {
    render(<VisitsList visits={[VISIT]} onOpen={noop} />);
    expect(screen.getByText("cola ×2")).toBeInTheDocument();
    expect(screen.getByText(/SIM-0001/)).toBeInTheDocument();
  });

  it("says when a sale's goods were not visible instead of implying none existed", () => {
    render(<VisitsList visits={[{ ...VISIT, items: [], receipt: null }]} onOpen={noop} />);
    expect(screen.getByText(t("pos.goodsNotVisible"))).toBeInTheDocument();
    expect(screen.getByText(t("pos.visitNoReceipt"))).toBeInTheDocument();
  });

  it("badges an administrative visit", () => {
    render(<VisitsList visits={[{ ...VISIT, kind: "administrative", items: [] }]} onOpen={noop} />);
    expect(screen.getByText(t("pos.visitAdministrative"))).toBeInTheDocument();
  });

  it("renders the empty state", () => {
    render(<VisitsList visits={[]} onOpen={noop} />);
    expect(screen.getByText(t("pos.noVisits"))).toBeInTheDocument();
  });
});
