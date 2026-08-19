"use client";

import { useEffect, useRef, useState } from "react";
import { AddCircleIcon, PaperclipIcon, SendPlaneIcon } from "@/components/icons";
import { Lightbox, type LightboxState } from "@/components/lightbox";
import { PanelBody, PanelCard } from "@/components/panel-card";
import { PillButton } from "@/components/pill-button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  deleteChat,
  postChatStream,
  type ChatSurface,
  type ChatTurn,
  type ToolCallTrace,
} from "@/lib/api";
import { SpeechQueue, takeSentences } from "@/lib/speech";
import { cn } from "@/lib/utils";

import { useT } from "@/lib/locale";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  turn?: ChatTurn;
  error?: boolean;
}

const USER_BUBBLE = "rounded-[20px] rounded-br-[6px]";
const AI_BUBBLE = "rounded-[20px] rounded-bl-[6px]";

function shortTime(value: unknown): string {
  return typeof value === "string" && value.length >= 16
    ? value.slice(11, 16)
    : String(value ?? "");
}

function toolLabel(call: ToolCallTrace): string {
  const a = call.args;
  if (call.name === "find_events") {
    const zone = a.zone_name ? ` @${a.zone_name}` : "";
    const time =
      a.time_from && a.time_to ? ` ${shortTime(a.time_from)}–${shortTime(a.time_to)}` : "";
    return `find_events ${a.event_type ?? ""}${zone}${time}`;
  }
  if (call.name === "get_clips" || call.name === "verify_footage") {
    const ids = Array.isArray(a.event_ids) ? a.event_ids.length : 0;
    return `${call.name} [${ids}]`;
  }
  const rest = Object.entries(a)
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
  return rest ? `${call.name} ${rest}` : call.name;
}

function ToolChip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "text-ink-muted inline-flex h-6 items-center rounded-[20px] bg-[#f0f0f0] px-2 font-mono text-xs",
        className,
      )}
    >
      {children}
    </span>
  );
}

function AssistantExtras({
  turn,
  onOpenFrame,
}: {
  turn: ChatTurn;
  onOpenFrame: (url: string, caption?: string) => void;
}) {
  const t = useT();
  const frames = turn.events.filter((e) => e.snapshot_url);
  return (
    <>
      {turn.tool_calls.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {turn.degraded && (
            <ToolChip className="bg-amber-100 text-amber-700">{t("chat.degraded")}</ToolChip>
          )}
          {turn.tool_calls.map((call, i) => (
            <ToolChip key={i}>{toolLabel(call)}</ToolChip>
          ))}
        </div>
      )}

      {frames.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {frames.map((e) => {
            const caption = `${new Date(e.ts_start).toLocaleTimeString()} · ${e.zone_name ?? "—"} (${e.type})`;
            return (
              <button
                key={e.id}
                type="button"
                onClick={() => onOpenFrame(e.snapshot_url!, caption)}
                className="cursor-zoom-in"
                title={caption}
              >
                <img
                  src={e.snapshot_url!}
                  alt={`${e.type} ${e.zone_name ?? ""}`}
                  loading="lazy"
                  className="h-[62px] w-[110px] rounded-[4px] object-cover"
                />
              </button>
            );
          })}
        </div>
      )}

      {turn.clips.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {turn.clips.map((c) => (
            <figure key={c.event_id} className="flex flex-col gap-1">
              <video src={c.url} controls preload="metadata" className="w-full rounded-[6px]" />
              <figcaption className="text-ink-muted text-xs">
                {new Date(c.ts_start).toLocaleString()}
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      {turn.events.length > 0 && (
        <details className="border-hairline rounded-[6px] border bg-white">
          <summary className="text-ink-muted hover:text-foreground cursor-pointer px-3 py-2 text-xs">
            {t("chat.matchedEvents", { count: turn.events.length })}
          </summary>
          <div className="border-hairline max-h-72 overflow-auto border-t">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("chat.colTime")}</TableHead>
                  <TableHead>{t("chat.colType")}</TableHead>
                  <TableHead>{t("chat.colZone")}</TableHead>
                  <TableHead>{t("chat.colDetails")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {turn.events.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell className="text-xs whitespace-nowrap">
                      {new Date(e.ts_start).toLocaleTimeString()}
                    </TableCell>
                    <TableCell className="text-xs">{e.type}</TableCell>
                    <TableCell className="text-xs">{e.zone_name ?? "—"}</TableCell>
                    <TableCell className="text-ink-muted text-xs">
                      {Object.entries(e.attributes)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(" ")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </details>
      )}
    </>
  );
}

export interface ChatPanelProps {
  title?: string;
  placeholder?: string;
  examples?: string[];
  emptyHint?: string;
  className?: string;

  compact?: boolean;

  surface?: ChatSurface;
}

export function ChatPanel({
  title,
  placeholder,
  examples = [],
  emptyHint,
  className,
  compact = false,
  surface = "ask",
}: ChatPanelProps) {
  const t = useT();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const [streaming, setStreaming] = useState("");
  const [liveTools, setLiveTools] = useState<ToolCallTrace[]>([]);
  const [copied, setCopied] = useState(false);
  const [muted, setMuted] = useState(false);
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);

  const speechBuffer = useRef("");
  const speech = useRef<SpeechQueue | null>(null);
  const sessionId = useRef("");
  if (!sessionId.current && typeof crypto !== "undefined") {
    sessionId.current = crypto.randomUUID();
  }
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading, streaming]);

  useEffect(() => {
    if (!copied) return;
    const id = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(id);
  }, [copied]);

  useEffect(() => {
    return () => {
      speech.current?.stop();
      speech.current = null;
    };
  }, []);

  async function send(q?: string) {
    const text = (q ?? input).trim();
    if (!text || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setLoading(true);
    setStreaming("");
    setLiveTools([]);

    speech.current?.stop();
    speech.current = muted ? null : new SpeechQueue();
    speechBuffer.current = "";

    const speakFrom = (chunk: string, flush = false) => {
      if (!speech.current) return;
      speechBuffer.current += chunk;
      const { sentences, rest } = takeSentences(speechBuffer.current);
      speechBuffer.current = rest;
      for (const s of sentences) speech.current.push(s);
      if (flush && rest.trim()) {
        speech.current.push(rest.trim());
        speechBuffer.current = "";
      }
    };

    try {
      for await (const ev of postChatStream(sessionId.current, text, surface)) {
        switch (ev.type) {
          case "delta":
            setStreaming((s) => s + ev.text);
            speakFrom(ev.text);
            break;
          case "reset":
            setStreaming("");
            speechBuffer.current = "";
            break;
          case "tool":
            setLiveTools((t) => [...t, { name: ev.name, args: ev.args }]);
            break;
          case "done":
            speakFrom("", true);
            setMessages((m) => [
              ...m,
              { role: "assistant", text: ev.turn.answer_text, turn: ev.turn },
            ]);
            break;
          case "error":
            setMessages((m) => [...m, { role: "assistant", text: ev.message, error: true }]);
            break;
        }
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: String(e), error: true }]);
    } finally {
      setLoading(false);
      setStreaming("");
      setLiveTools([]);
    }
  }

  function newChat() {
    if (loading) return;
    speech.current?.stop();
    speech.current = null;
    deleteChat(sessionId.current).catch(() => {});
    sessionId.current = crypto.randomUUID();
    setMessages([]);
  }

  function toggleMute() {
    setMuted((m) => {
      if (!m) {
        speech.current?.stop();
        speech.current = null;
      }
      return !m;
    });
  }

  async function share() {
    const transcript = messages
      .map((m) => `${m.role === "user" ? "You" : "Vision24"}: ${m.text}`)
      .join("\n\n");
    try {
      await navigator.clipboard.writeText(transcript);
      setCopied(true);
    } catch {}
  }

  return (
    <div className={cn("flex min-h-0 flex-col gap-2", className)}>
      {lightbox && (
        <Lightbox url={lightbox.url} caption={lightbox.caption} onClose={() => setLightbox(null)} />
      )}

      {title && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className={cn("font-semibold", compact ? "text-base" : "text-2xl leading-7")}>
            {title}
          </h1>
          <div className="flex items-center gap-1.5">
            <PillButton
              variant="quiet"
              onClick={toggleMute}
              aria-label={muted ? t("chat.unmute") : t("chat.mute")}
              title={muted ? t("chat.unmute") : t("chat.mute")}
              aria-pressed={!muted}
            >
              <span aria-hidden>{muted ? "🔇" : "🔊"}</span>
              {!compact && (muted ? t("chat.muted") : t("chat.speaking"))}
            </PillButton>
            {!compact && (
              <PillButton
                variant="quiet"
                onClick={share}
                disabled={messages.length === 0}
                title={t("chat.copyHint")}
              >
                {copied ? "Copied" : "Share"}
              </PillButton>
            )}
            <PillButton variant="primary" onClick={newChat} disabled={messages.length === 0}>
              {t("chat.newChat")}
              <AddCircleIcon />
            </PillButton>
          </div>
        </div>
      )}

      <PanelCard className="min-h-0 flex-1">
        <PanelBody className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
          {messages.length === 0 && !loading && (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 py-8 text-center">
              {emptyHint && (
                <p className="text-ink-muted max-w-md text-xs leading-4 font-medium">{emptyHint}</p>
              )}
              <div className="flex flex-wrap justify-center gap-1.5">
                {examples.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => send(ex)}
                    className="border-hairline text-ink-muted hover:text-foreground rounded-[20px] border bg-white px-2.5 py-1 text-xs transition-colors"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div
                  className={cn(
                    "border-surface-border max-w-[80%] border bg-black bg-linear-to-b from-[rgba(26,26,26,0)] to-[rgba(102,102,102,0.7)] p-3 text-sm font-medium text-white",
                    USER_BUBBLE,
                  )}
                >
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={i} className="flex justify-start">
                <div
                  className={cn(
                    "flex w-full max-w-[606px] flex-col gap-3 border p-3",
                    AI_BUBBLE,
                    m.error
                      ? "border-danger-line bg-danger-line/5 text-danger-ink"
                      : "border-surface-border bg-[#f5f5f5] bg-linear-to-b from-white/0 to-white/70",
                  )}
                >
                  <p className="font-mono text-sm leading-normal whitespace-pre-wrap">{m.text}</p>
                  {m.turn && (
                    <AssistantExtras
                      turn={m.turn}
                      onOpenFrame={(url, caption) => setLightbox({ url, caption })}
                    />
                  )}
                </div>
              </div>
            ),
          )}

          {loading && (
            <div className="flex justify-start">
              <div
                className={cn(
                  "border-surface-border flex w-full max-w-[606px] flex-col gap-3 border bg-[#f5f5f5] bg-linear-to-b from-white/0 to-white/70 p-3",
                  AI_BUBBLE,
                )}
              >
                {liveTools.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {liveTools.map((call, i) => (
                      <ToolChip key={i}>{toolLabel(call)}</ToolChip>
                    ))}
                  </div>
                )}
                {streaming ? (
                  <p className="font-mono text-sm leading-normal whitespace-pre-wrap">
                    {streaming}
                  </p>
                ) : (
                  <div className="flex items-center gap-1.5 py-0.5">
                    <span className="bg-ink-muted size-1.5 animate-bounce rounded-full [animation-delay:0ms]" />
                    <span className="bg-ink-muted size-1.5 animate-bounce rounded-full [animation-delay:150ms]" />
                    <span className="bg-ink-muted size-1.5 animate-bounce rounded-full [animation-delay:300ms]" />
                  </div>
                )}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </PanelBody>
      </PanelCard>

      <PanelCard className="shrink-0">
        <PanelBody className="flex items-center gap-2">
          <textarea
            aria-label={placeholder}
            placeholder={placeholder}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}

            className="placeholder:text-ink-muted focus-visible:ring-ring field-sizing-content max-h-32 min-h-[38px] flex-1 resize-none self-center rounded-lg border-0 bg-transparent py-2 text-base font-medium outline-none focus-visible:ring-2"
          />

          <button
            type="button"
            disabled
            aria-label={t("chat.attachments")}
            title={t("chat.attachments")}
            className="border-surface-border text-foreground inline-flex size-[38px] shrink-0 items-center justify-center rounded-[20px] border bg-white disabled:opacity-40"
          >
            <PaperclipIcon />
          </button>
          <PillButton variant="send" onClick={() => send()} disabled={loading || !input.trim()}>
            {loading ? "…" : "Send"}
            <SendPlaneIcon />
          </PillButton>
        </PanelBody>
      </PanelCard>
    </div>
  );
}
