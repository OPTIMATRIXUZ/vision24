"use client";

import { ChatPanel } from "@/components/chat-panel";
import { GlowBackdrop } from "@/components/glow-backdrop";

import { useT, type TFunc } from "@/lib/locale";

const examples = (t: TFunc) => [t("ask.q1"), t("ask.q2"), t("ask.q3"), t("ask.q4")];

export default function AskPage() {
  const t = useT();
  return (
    <>
      <GlowBackdrop src="/blue-glow.svg" />
      <ChatPanel
        className="h-[calc(100vh-190px)] min-h-[520px]"
        title={t("ask.title")}
        placeholder={t("ask.placeholder")}
        examples={examples(t)}
        emptyHint={t("ask.hint")}
      />
    </>
  );
}
