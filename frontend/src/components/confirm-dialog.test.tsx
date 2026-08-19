// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./confirm-dialog";
import { t } from "@/lib/i18n";

function Harness({
  onConfirm = () => Promise.resolve(),
  confirmPhrase,
}: {
  onConfirm?: () => Promise<unknown>;
  confirmPhrase?: string;
}) {
  const [open, setOpen] = useState(true);
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={setOpen}
      title="Delete everything?"
      description="There is no undo."
      confirmLabel="Delete"
      confirmPhrase={confirmPhrase}
      onConfirm={onConfirm}
    />
  );
}

describe("ConfirmDialog", () => {
  it("names itself, so a screen reader announces what is being confirmed", async () => {
    render(<Harness />);
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveAccessibleName("Delete everything?");
    expect(dialog).toHaveAccessibleDescription("There is no undo.");
  });

  it("moves focus into the dialog", async () => {
    render(<Harness />);
    const dialog = await screen.findByRole("alertdialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await screen.findByRole("alertdialog");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("keeps confirm disabled until the phrase matches exactly", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<Harness confirmPhrase="demo" onConfirm={onConfirm} />);

    const confirmButton = await screen.findByRole("button", { name: "Delete" });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByRole("textbox"), "dem");
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByRole("textbox"), "o");
    await waitFor(() => expect(confirmButton).toBeEnabled());

    await user.click(confirmButton);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("confirms and closes when no phrase is required", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<Harness onConfirm={onConfirm} />);

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Delete" }));

    expect(onConfirm).toHaveBeenCalledOnce();

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("disables both buttons while the action is in flight", async () => {
    let release: () => void = () => {};
    const onConfirm = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<Harness onConfirm={onConfirm} />);

    await user.click(await screen.findByRole("button", { name: "Delete" }));

    const cancel = screen.getByRole("button", { name: t("common.cancel") });
    await waitFor(() => expect(cancel).toBeDisabled());
    expect(screen.getByRole("button", { name: t("common.working") })).toBeDisabled();

    release();
  });

  it("stays open when the action fails, so failure does not read as success", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<Harness onConfirm={onConfirm} />);

    await user.click(await screen.findByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled());
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });
});
