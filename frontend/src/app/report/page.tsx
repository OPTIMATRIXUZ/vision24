"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ErrorNote } from "@/components/error-note";
import {
  Chip,
  PanelBody,
  PanelCard,
  PanelHeader,
  PanelSubtitle,
  PanelTitle,
  PanelTitleRow,
} from "@/components/panel-card";
import { PillButton } from "@/components/pill-button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { getReport, type Report } from "@/lib/api";

import { useT } from "@/lib/locale";

const markdownComponents = {
  h1: (props: React.ComponentProps<"h1">) => (
    <h1 className="mb-3 text-xl font-semibold" {...props} />
  ),
  h2: (props: React.ComponentProps<"h2">) => (
    <h2 className="mt-5 mb-2 border-b pb-1 text-base font-semibold" {...props} />
  ),
  h3: (props: React.ComponentProps<"h3">) => (
    <h3 className="mt-4 mb-1.5 text-sm font-semibold" {...props} />
  ),
  p: (props: React.ComponentProps<"p">) => (
    <p className="mb-2 text-sm leading-relaxed" {...props} />
  ),
  ul: (props: React.ComponentProps<"ul">) => (
    <ul className="mb-3 list-disc space-y-1 pl-5 text-sm" {...props} />
  ),
  ol: (props: React.ComponentProps<"ol">) => (
    <ol className="mb-3 list-decimal space-y-1 pl-5 text-sm" {...props} />
  ),
  table: (props: React.ComponentProps<"table">) => (
    <div className="mb-3 overflow-x-auto">
      <table className="w-full border-collapse text-sm" {...props} />
    </div>
  ),
  th: (props: React.ComponentProps<"th">) => (
    <th className="border-b bg-neutral-50 px-3 py-1.5 text-left font-medium" {...props} />
  ),
  td: (props: React.ComponentProps<"td">) => <td className="border-b px-3 py-1.5" {...props} />,
};

export default function ReportPage() {
  const t = useT();
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function load(refresh = false) {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      setReport(await getReport(day, refresh));
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
        <h1 className="text-2xl leading-7 font-semibold">{t("nav.report")}</h1>
        <div className="flex flex-wrap items-center gap-1.5">
          <Input
            type="date"
            aria-label={t("report.date")}
            value={day}
            onChange={(e) => setDay(e.target.value)}
            className="h-[38px] w-40 rounded-[20px]"
          />
          <PillButton variant="primary" onClick={() => load(false)} disabled={loading}>
            {loading ? t("report.generating") : t("report.generate")}
          </PillButton>
          {report && (
            <>
              <PillButton variant="quiet" onClick={() => load(true)} disabled={loading}>
                {t("report.refresh")}
              </PillButton>
              <PillButton variant="quiet" onClick={() => window.print()}>
                {t("report.print")}
              </PillButton>
            </>
          )}
        </div>
      </div>

      <ErrorNote error={error} />

      {loading && (
        <PanelCard className="max-w-3xl">
          <PanelBody className="flex flex-col gap-2 py-2">
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-32 w-full" />
          </PanelBody>
        </PanelCard>
      )}

      {report && !loading && (
        <PanelCard className="max-w-3xl">
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("report.title", { day: report.day })}</PanelTitle>
              {report.generated_by === "fallback" && (
                <Chip className="bg-amber-50 text-amber-700">{t("report.fallback")}</Chip>
              )}
            </PanelTitleRow>
            <PanelSubtitle className="print:hidden">{t("report.note")}</PanelSubtitle>
          </PanelHeader>
          <PanelBody className="text-foreground">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {report.markdown}
            </ReactMarkdown>
          </PanelBody>
        </PanelCard>
      )}

      {!report && !loading && (
        <PanelCard className="max-w-3xl print:hidden">
          <PanelHeader>
            <PanelTitleRow>
              <PanelTitle>{t("report.emptyTitle")}</PanelTitle>
            </PanelTitleRow>
            <PanelSubtitle>{t("report.note")}</PanelSubtitle>
          </PanelHeader>
        </PanelCard>
      )}
    </div>
  );
}
