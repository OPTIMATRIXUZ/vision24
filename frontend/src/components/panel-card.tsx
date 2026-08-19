import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function PanelCard({ className, ...props }: React.ComponentProps<typeof Card>) {
  return (
    <Card
      className={cn("border-surface-border bg-surface rounded-[16px] border ring-0", className)}
      {...props}
    />
  );
}

function PanelHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-2 px-(--card-spacing)", className)} {...props} />;
}

function PanelTitleRow({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-wrap items-center gap-2", className)} {...props} />;
}

function PanelTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return (
    <h2
      className={cn("text-foreground text-[20px] leading-tight font-semibold", className)}
      {...props}
    />
  );
}

function PanelSubtitle({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("text-ink-muted text-xs leading-4 font-medium", className)} {...props} />;
}

function Chip({ className, ...props }: React.ComponentProps<typeof Badge>) {
  return (
    <Badge
      variant="secondary"
      className={cn(
        "bg-chip text-ink-muted h-6 rounded-[20px] px-2 text-xs font-medium",
        className,
      )}
      {...props}
    />
  );
}

function PanelBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-(--card-spacing)", className)} {...props} />;
}

export { PanelCard, PanelHeader, PanelTitleRow, PanelTitle, PanelSubtitle, PanelBody, Chip };
