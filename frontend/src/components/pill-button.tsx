import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const pillVariants = cva(
  "inline-flex h-[38px] shrink-0 items-center justify-center gap-1.5 border px-[11px] text-sm font-medium whitespace-nowrap transition-colors outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:pointer-events-none disabled:opacity-50 [&_img]:size-5 [&_svg]:size-5",
  {
    variants: {
      variant: {
        neutral: "border-hairline bg-white text-foreground hover:bg-neutral-50",
        quiet: "border-surface-border bg-white text-foreground hover:bg-neutral-50",
        primary: "border-white bg-brand-deep text-white hover:bg-brand-deep/90",
        dark: "border-black bg-black text-white hover:bg-neutral-800",
        info: "border-info-line bg-white text-foreground hover:bg-info-line/10",
        danger: "border-danger-line bg-white text-danger-ink hover:bg-danger-line/10",
        dangerQuiet: "border-danger-line bg-white text-foreground hover:bg-danger-line/10",
        send: "border-[#75a3f7] bg-white text-brand hover:bg-brand/5",
        active: "border-brand-deep bg-white text-brand-deep hover:bg-brand-deep/5",

        next: "border-info-line bg-white text-brand-deep hover:bg-brand-deep/5",
      },
      shape: {
        pill: "rounded-[20px]",
        field: "rounded-[8px]",
      },
    },
    defaultVariants: { variant: "neutral", shape: "pill" },
  },
);

function PillButton({
  className,
  variant,
  shape,
  ...props
}: React.ComponentProps<"button"> & VariantProps<typeof pillVariants>) {
  return (
    <button type="button" className={cn(pillVariants({ variant, shape }), className)} {...props} />
  );
}

export { PillButton, pillVariants };
