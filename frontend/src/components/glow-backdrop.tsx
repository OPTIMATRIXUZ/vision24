import { cn } from "@/lib/utils";

export function GlowBackdrop({
  src = "/gradient-blobs.svg",
  className,
}: {
  src?: string;
  className?: string;
}) {
  return (
    <div
      aria-hidden
      className={cn("pointer-events-none fixed inset-0 -z-10 overflow-hidden", className)}
    >
      <img
        src={src}
        alt=""
        className="absolute bottom-[-386px] left-[calc(50%-29px)] h-[956px] w-[2592px] max-w-none -translate-x-1/2"
      />
    </div>
  );
}
