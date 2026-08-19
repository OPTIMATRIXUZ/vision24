"use client";

import { MediaViewer } from "@/components/media-viewer";

export interface LightboxState {
  url: string;
  caption?: string;
}

export function Lightbox({
  url,
  caption,
  onClose,
}: {
  url: string;
  caption?: string;
  onClose: () => void;
}) {
  return (
    <MediaViewer open onOpenChange={(next) => !next && onClose()} caption={caption}>
      <img
        src={url}
        alt={caption ?? "frame"}
        className="max-h-[85vh] max-w-[92vw] rounded-lg object-contain shadow-2xl"
      />
    </MediaViewer>
  );
}
