// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatAmount, ReceiptCard } from "./receipt-card";
import type { PosReceipt } from "@/lib/api";
import { t } from "@/lib/i18n";

const RECEIPT: PosReceipt = {
  id: "r1",
  external_id: "SIM-20260803-0001",
  kind: "sale",
  ts: "2026-08-03T10:00:12+05:00",
  total: 45_000,
  items: [{ sku: "4780001", name: "Coca-Cola 1.5L", qty: 3, unit_price: 15_000 }],
  zone_id: null,
  zone_name: null,
  source: "simulated",
  flag: null,
};

describe("formatAmount", () => {
  it("groups integer UZS with spaces", () => {
    expect(formatAmount(1_500_000)).toMatch(/^1.500.000$/);
  });
});

describe("ReceiptCard", () => {
  it("prints the slip: id, item line and total", () => {
    render(<ReceiptCard receipt={RECEIPT} />);
    expect(screen.getByText("SIM-20260803-0001")).toBeInTheDocument();
    expect(screen.getByText("Coca-Cola 1.5L")).toBeInTheDocument();
    expect(screen.getByText(/3 ×/)).toBeInTheDocument();
    expect(screen.getByText(/45.000/)).toBeInTheDocument();
  });

  it("marks a simulated receipt as such", () => {
    render(<ReceiptCard receipt={RECEIPT} />);

    expect(screen.getByText(t("pos.simulated"))).toBeInTheDocument();
  });

  it("wears the danger edge when flagged", () => {
    const { rerender } = render(<ReceiptCard receipt={RECEIPT} />);
    expect(screen.getByTestId("receipt-card").className).not.toContain("border-danger-line");
    rerender(<ReceiptCard receipt={{ ...RECEIPT, flag: "no_person_at_sale" }} />);
    expect(screen.getByTestId("receipt-card").className).toContain("border-danger-line");
  });

  it("strikes through a void", () => {
    render(<ReceiptCard receipt={{ ...RECEIPT, kind: "void" }} />);
    const list = screen.getByText("Coca-Cola 1.5L").closest("ul");
    expect(list?.className).toContain("line-through");
  });
});
