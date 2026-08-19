// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { t } from "@/lib/i18n";

const getProducts = vi.fn();
const createProduct = vi.fn();
const deleteProduct = vi.fn();
const addProductSample = vi.fn();
const deleteProductSample = vi.fn();

vi.mock("@/lib/api", () => ({
  getProducts: (...args: unknown[]) => getProducts(...args),
  createProduct: (...args: unknown[]) => createProduct(...args),
  deleteProduct: (...args: unknown[]) => deleteProduct(...args),
  addProductSample: (...args: unknown[]) => addProductSample(...args),
  deleteProductSample: (...args: unknown[]) => deleteProductSample(...args),
}));

const { ProductCatalog } = await import("./product-catalog");

const COLA = {
  id: "p1",
  name: "Cola crate",
  units_per_package: 24,
  unit_label: "бутылок",
  samples: [{ id: "s1", url: "https://fake/product-samples/p1/a.jpg" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  getProducts.mockResolvedValue([COLA]);
  createProduct.mockResolvedValue({ ...COLA, id: "p2", name: "Chips box", samples: [] });
});

describe("ProductCatalog", () => {
  it("lists products with their sample photos", async () => {
    render(<ProductCatalog />);
    expect(await screen.findByText("Cola crate")).toBeInTheDocument();
    expect(screen.getByAltText("Cola crate")).toHaveAttribute(
      "src",
      "https://fake/product-samples/p1/a.jpg",
    );
  });

  it("creates a product from the form", async () => {
    const user = userEvent.setup();
    render(<ProductCatalog />);
    await screen.findByText("Cola crate");

    await user.type(screen.getByLabelText(t("products.name")), "Chips box");
    await user.type(screen.getByLabelText(t("products.unitsPerPackage")), "12");
    await user.type(screen.getByLabelText(t("products.unitLabel")), "пачек");
    await user.click(screen.getByRole("button", { name: t("products.add") }));

    await waitFor(() =>
      expect(createProduct).toHaveBeenCalledWith({
        name: "Chips box",
        units_per_package: 12,
        unit_label: "пачек",
      }),
    );

    expect(getProducts).toHaveBeenCalledTimes(2);
  });

  it("shows the empty state when there are no products", async () => {
    getProducts.mockResolvedValue([]);
    render(<ProductCatalog />);
    expect(await screen.findByText(t("products.empty"))).toBeInTheDocument();
  });

  it("asks for confirmation before deleting a product", async () => {
    const user = userEvent.setup();
    deleteProduct.mockResolvedValue({ deleted: "p1" });
    render(<ProductCatalog />);
    await screen.findByText("Cola crate");

    await user.click(screen.getByRole("button", { name: t("products.delete") }));

    expect(deleteProduct).not.toHaveBeenCalled();
    expect(
      await screen.findByText(t("products.deleteTitle", { name: "Cola crate" })),
    ).toBeInTheDocument();
  });
});
