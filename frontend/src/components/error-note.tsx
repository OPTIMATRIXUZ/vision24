"use client";

import { ApiError } from "@/lib/api-error";

import { useT } from "@/lib/locale";

export function ErrorNote({ error, className }: { error: unknown; className?: string }) {
  const t = useT();
  if (!error) return null;

  const api = error instanceof ApiError ? error : null;
  const message = api ? api.message : String(error);

  const showRequestId = !!api && api.status >= 500 && !!api.requestId;

  return (
    <div
      role="alert"
      aria-live="polite"
      className={[
        "rounded-lg border px-3 py-2 text-sm",
        "border-red-200 bg-red-50 text-red-900",
        className ?? "",
      ].join(" ")}
    >
      <p>{message}</p>
      {showRequestId && (
        <p className="mt-1 font-mono text-xs opacity-70">
          {t("error.reference")} {api.requestId}
        </p>
      )}
    </div>
  );
}
