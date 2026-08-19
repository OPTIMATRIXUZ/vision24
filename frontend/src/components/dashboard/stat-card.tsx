import { PanelBody, PanelCard, PanelHeader } from "@/components/panel-card";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  suffix,
  meta,
  imageUrl,
  onOpen,
  className,
}: {
  label: string;
  value: string | number;

  suffix?: string;

  meta?: string;
  imageUrl?: string | null;
  onOpen?: (url: string, caption?: string) => void;
  className?: string;
}) {
  return (
    <PanelCard className={cn("min-w-[220px] flex-1", className)}>
      <PanelHeader>
        <div className="text-ink-muted text-base leading-tight font-semibold">{label}</div>
        <div className="flex items-end gap-2">
          <span className="text-foreground text-[36px] leading-[34px] font-semibold tabular-nums">
            {value}
          </span>
          {(meta || suffix) && (
            <div className="flex min-w-0 flex-col gap-0.5 text-xs leading-4 font-medium">
              {meta && <span className="text-ink-faint truncate">{meta}</span>}
              {suffix && <span className="text-ink-muted truncate">{suffix}</span>}
            </div>
          )}
        </div>
      </PanelHeader>
      {imageUrl && (
        <PanelBody className="mt-auto">
          <button
            type="button"
            onClick={() => onOpen?.(imageUrl, label)}
            className="block w-full cursor-zoom-in"
          >
            <img
              src={imageUrl}
              alt={label}
              loading="lazy"
              className="aspect-[212/118] w-full rounded-[6px] object-cover"
            />
          </button>
        </PanelBody>
      )}
    </PanelCard>
  );
}
