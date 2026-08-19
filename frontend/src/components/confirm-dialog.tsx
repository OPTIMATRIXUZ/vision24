"use client";

import { useId, useState } from "react";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { PillButton } from "@/components/pill-button";

import { useT } from "@/lib/locale";

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  confirmPhrase,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;

  confirmPhrase?: string;
  onConfirm: () => Promise<unknown>;
}) {
  const t = useT();
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const inputId = useId();

  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setTyped("");
  }

  const armed = confirmPhrase === undefined || typed.trim() === confirmPhrase;

  async function confirm() {
    setPending(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch {
    } finally {
      setPending(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>

        {confirmPhrase !== undefined && (
          <div className="flex flex-col gap-1.5">
            <label htmlFor={inputId} className="text-sm font-medium">
              {t("common.typeToConfirm", { phrase: confirmPhrase })}
            </label>
            <input
              id={inputId}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={pending}
              autoComplete="off"
              className="border-hairline focus-visible:border-brand focus-visible:ring-brand/40 h-9 w-full rounded-[8px] border bg-white px-2.5 text-sm outline-none focus-visible:ring-2 disabled:opacity-50"
            />
          </div>
        )}

        <AlertDialogFooter>
          <PillButton variant="quiet" onClick={() => onOpenChange(false)} disabled={pending}>
            {t("common.cancel")}
          </PillButton>
          <PillButton variant="danger" onClick={confirm} disabled={!armed || pending}>
            {pending ? t("common.working") : confirmLabel}
          </PillButton>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
