"use client";

import { Dialog } from "@base-ui/react/dialog";

import { useT } from "@/lib/locale";

export function MediaViewer({
  open,
  onOpenChange,
  caption,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  caption?: string;
  children: React.ReactNode;
}) {
  const t = useT();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 min-h-dvh bg-black/85 transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Dialog.Popup className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 p-6 transition-opacity duration-100 data-ending-style:opacity-0 data-starting-style:opacity-0">
          <Dialog.Title className="sr-only">{caption ?? t("media.title")}</Dialog.Title>
          {children}
          {caption && <p className="text-sm text-neutral-200">{caption}</p>}
          <Dialog.Close className="focus-visible:ring-ring absolute top-4 right-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white hover:bg-white/20 focus-visible:ring-2 focus-visible:outline-none">
            {t("media.close")}
          </Dialog.Close>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
