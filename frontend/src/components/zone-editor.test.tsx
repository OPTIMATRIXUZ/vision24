// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { t } from "@/lib/i18n";

const api = vi.fn();
const apiBlob = vi.fn();

vi.mock("@/lib/api", () => ({
  api: (...args: unknown[]) => api(...args),
  apiBlob: (...args: unknown[]) => apiBlob(...args),
}));

const { ZoneEditor } = await import("./zone-editor");

beforeEach(() => {
  api.mockResolvedValue([]);
  apiBlob.mockResolvedValue(new Blob([""], { type: "image/jpeg" }));

  globalThis.URL.createObjectURL = vi.fn(() => "blob:frame");
  globalThis.URL.revokeObjectURL = vi.fn();
});

function vertexCount(container: HTMLElement): number {
  return container.querySelectorAll("circle").length;
}

describe("ZoneEditor keyboard drawing", () => {
  it("exposes the frame as a focusable, named control", async () => {
    render(<ZoneEditor cameraId="cam-1" />);
    const canvas = await screen.findByRole("application", { name: t("zones.canvasLabel") });
    expect(canvas).toHaveAttribute("tabindex", "0");
  });

  it("places a point with Enter and removes it with Backspace", async () => {
    const user = userEvent.setup();
    const { container } = render(<ZoneEditor cameraId="cam-1" />);
    const canvas = await screen.findByRole("application");

    canvas.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(vertexCount(container)).toBe(1));

    await user.keyboard("{ArrowRight}{ArrowDown}{Enter}");
    await waitFor(() => expect(vertexCount(container)).toBe(2));

    await user.keyboard("{Backspace}");
    await waitFor(() => expect(vertexCount(container)).toBe(1));

    await user.keyboard("{Escape}");
    await waitFor(() => expect(vertexCount(container)).toBe(0));
  });

  it("moves the crosshair, so two points do not land on the same spot", async () => {
    const user = userEvent.setup();
    const { container } = render(<ZoneEditor cameraId="cam-1" />);
    const canvas = await screen.findByRole("application");

    canvas.focus();
    await user.keyboard("{Enter}{ArrowRight}{ArrowRight}{Enter}");
    await waitFor(() => expect(vertexCount(container)).toBe(2));

    const [first, second] = [...container.querySelectorAll("circle")].map((c) =>
      c.getAttribute("cx"),
    );
    expect(first).not.toEqual(second);
  });

  it("announces the crosshair position", async () => {
    const user = userEvent.setup();
    render(<ZoneEditor cameraId="cam-1" />);
    const canvas = await screen.findByRole("application");

    canvas.focus();
    await user.keyboard("{ArrowRight}");
    await waitFor(() =>
      expect(screen.getByText(t("zones.cursorAt", { x: 51, y: 50, count: 0 }))).toBeInTheDocument(),
    );
  });
});
