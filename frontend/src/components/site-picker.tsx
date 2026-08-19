"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getSelectedSite, listSites, setSelectedSite, type SiteSummary } from "@/lib/api";

import { useT } from "@/lib/locale";

export function SitePicker() {
  const t = useT();
  const router = useRouter();
  const [sites, setSites] = useState<SiteSummary[]>([]);
  const [current, setCurrent] = useState("");

  useEffect(() => {
    let cancelled = false;
    listSites()
      .then((found) => {
        if (cancelled) return;
        setSites(found);
        const stored = getSelectedSite();

        const valid = found.some((s) => s.id === stored);
        if (stored && !valid) setSelectedSite("");
        setCurrent(valid ? stored : (found[0]?.id ?? ""));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (sites.length < 2) return null;

  function onChange(id: string | null) {
    if (!id) return;
    setCurrent(id);

    setSelectedSite(id === sites[0]?.id ? "" : id);
    router.refresh();
  }

  return (
    <Select
      items={sites.map((site) => ({ value: site.id, label: site.name }))}
      value={current}
      onValueChange={onChange}
    >
      <SelectTrigger className="max-w-[16ch] shrink-0" aria-label={t("session.site")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {sites.map((site) => (
          <SelectItem key={site.id} value={site.id}>
            {site.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
