// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { t } from "@/lib/i18n";

const listSites = vi.fn();
const getSelectedSite = vi.fn();
const setSelectedSite = vi.fn();
const refresh = vi.fn();

vi.mock("@/lib/api", () => ({
  listSites: () => listSites(),
  getSelectedSite: () => getSelectedSite(),
  setSelectedSite: (id: string) => setSelectedSite(id),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

const { SitePicker } = await import("./site-picker");

const SITES = [
  { id: "11111111-1111-1111-1111-111111111111", name: "Demo Store" },
  { id: "22222222-2222-2222-2222-222222222222", name: "Warehouse" },
];

beforeEach(() => {
  getSelectedSite.mockReturnValue("");
  listSites.mockResolvedValue(SITES);
});

describe("SitePicker", () => {
  it("shows the site name, not the id", async () => {
    render(<SitePicker />);
    expect(await screen.findByText("Demo Store")).toBeInTheDocument();
    expect(screen.queryByText(SITES[0].id)).not.toBeInTheDocument();
  });

  it("has an accessible name", async () => {
    render(<SitePicker />);
    const trigger = await screen.findByRole("combobox");
    expect(trigger).toHaveAccessibleName(t("session.site"));
  });

  it("stays hidden while the tenant has a single site", async () => {
    listSites.mockResolvedValue([SITES[0]]);
    const { container } = render(<SitePicker />);
    await waitFor(() => expect(listSites).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("drops a stored site that no longer exists", async () => {
    getSelectedSite.mockReturnValue("99999999-9999-9999-9999-999999999999");
    render(<SitePicker />);
    await waitFor(() => expect(setSelectedSite).toHaveBeenCalledWith(""));
    expect(await screen.findByText("Demo Store")).toBeInTheDocument();
  });
});
