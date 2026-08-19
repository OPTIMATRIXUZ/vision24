// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SavingsCard } from "./savings-card";
import type { Savings } from "@/lib/api";
import { t } from "@/lib/i18n";

const SAVINGS: Savings = {
  month: "2026-08",
  lines: [
    { key: "queues", count: 2, unit_value: 120_000, amount: 240_000 },
    { key: "after_hours", count: 1, unit_value: 500_000, amount: 500_000 },
    { key: "deliveries", count: 0, unit_value: 80_000, amount: 0 },
    { key: "pos", count: 3, unit_value: 150_000, amount: 812_000 },
  ],
  total: 1_552_000,
  subscription: 1_500_000,
  net: 52_000,
  constants: { avg_check: 120_000 },
};

describe("SavingsCard", () => {
  it("shows the net figure and the month", () => {
    render(<SavingsCard savings={SAVINGS} />);
    expect(screen.getByText("2026-08")).toBeInTheDocument();

    expect(screen.getByText(/\+52.000/)).toBeInTheDocument();
  });

  it("shows each line's own arithmetic — the formula is the pitch", () => {
    render(<SavingsCard savings={SAVINGS} />);

    expect(screen.getByText(t("savings.line.queues"))).toBeInTheDocument();
    expect(screen.getByText(/2 × 120.000/)).toBeInTheDocument();
    expect(screen.getByText(/812.000/)).toBeInTheDocument();
    expect(screen.getByText(/−1.500.000/)).toBeInTheDocument();
  });

  it("colors a negative net as a loss, not a saving", () => {
    render(<SavingsCard savings={{ ...SAVINGS, net: -55_000 }} />);
    const net = screen.getByText(/55.000/);
    expect(net.closest("div")?.className).toContain("text-danger-ink");
  });
});
