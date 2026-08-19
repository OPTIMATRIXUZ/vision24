// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { t } from "@/lib/i18n";

import { DiscrepancyList } from "./discrepancy-list";
import type { PosDiscrepancy } from "@/lib/api";

const noop = () => {};

const FLAG: PosDiscrepancy = {
  flag: "no_person_at_sale",
  status: "open",
  ts: "2026-08-03T11:00:00+05:00",
  ts_end: null,
  zone_name: "Касса",
  receipt: null,
  seen_items: null,
  evidence_event_id: null,
  snapshot_url: null,
  explanation: "Sale of 45 000 UZS at an empty checkout.",
};

describe("DiscrepancyList", () => {
  it("reports an all-clear when every receipt was checked", () => {
    render(<DiscrepancyList discrepancies={[]} onOpen={noop} />);
    expect(screen.getByText(t("pos.noSuspicious"))).toBeInTheDocument();
  });

  it("scopes the all-clear to the checked receipts when coverage was partial", () => {
    render(<DiscrepancyList discrepancies={[]} onOpen={noop} partial />);
    expect(screen.queryByText(t("pos.noSuspicious"))).not.toBeInTheDocument();
    expect(screen.getByText(t("pos.noSuspiciousPartial"))).toBeInTheDocument();
  });

  it("lists the flags themselves regardless of coverage", () => {
    render(<DiscrepancyList discrepancies={[FLAG]} onOpen={noop} partial />);
    expect(screen.getByText(t("pos.flag.no_person_at_sale"))).toBeInTheDocument();
    expect(screen.queryByText(t("pos.noSuspicious"))).not.toBeInTheDocument();
    expect(screen.queryByText(t("pos.noSuspiciousPartial"))).not.toBeInTheDocument();
  });

  it("mutes a cleared flag and says the AI dismissed it", () => {
    const cleared: PosDiscrepancy = {
      ...FLAG,
      flag: "unscanned_visit",
      status: "cleared",
      ts_end: "2026-08-03T11:00:24+05:00",
    };
    render(<DiscrepancyList discrepancies={[cleared]} onOpen={noop} />);
    const row = screen.getByTestId("discrepancy");
    expect(row.className).toContain("opacity-60");
    expect(row.className).not.toContain("border-danger-line");
    expect(screen.getByText(t("pos.clearedByAi"))).toBeInTheDocument();
  });

  it("names the goods the camera saw on an open flag", () => {
    const seen: PosDiscrepancy = {
      ...FLAG,
      flag: "unscanned_visit",
      seen_items: [{ name: "snack packet", qty: 1 }],
    };
    render(<DiscrepancyList discrepancies={[seen]} onOpen={noop} />);
    expect(screen.getByText(t("pos.cameraSaw", { items: "snack packet ×1" }))).toBeInTheDocument();
  });
});
