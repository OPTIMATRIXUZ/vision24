"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

import { useT } from "@/lib/locale";

export default function ErrorPage({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  const t = useT();
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex flex-col items-start gap-3 py-12" role="alert">
      <h1 className="text-xl font-semibold">{t("error.title")}</h1>
      <p className="text-ink-muted text-sm">{t("error.unexpected")}</p>
      {error.digest && (
        <p className="text-ink-muted font-mono text-xs">
          {t("error.reference")} {error.digest}
        </p>
      )}
      <Button onClick={() => unstable_retry()}>{t("error.retry")}</Button>
    </div>
  );
}
