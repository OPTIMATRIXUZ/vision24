import { synthesizeSpeech } from "@/lib/api";

const BOUNDARY = /([.!?…]+["'»)\]]*\s+)|(\n+)/gu;

const STARTS_SENTENCE = /[\p{Lu}«"'([]/u;

function speakable(text: string): boolean {
  return /\p{L}/u.test(text) && text.trim().length > 1;
}

export function takeSentences(buffer: string): { sentences: string[]; rest: string } {
  const sentences: string[] = [];
  const re = new RegExp(BOUNDARY);
  let start = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(buffer)) !== null) {
    const cut = m.index + m[0].length;
    if (m[2] === undefined) {
      const next = buffer[cut];
      if (next === undefined) break;
      if (!STARTS_SENTENCE.test(next)) continue;
    }
    const sentence = buffer.slice(start, cut).trim();
    if (speakable(sentence)) sentences.push(sentence);
    start = cut;
  }
  return { sentences, rest: buffer.slice(start) };
}

async function synthesize(text: string, signal: AbortSignal): Promise<string> {
  return URL.createObjectURL(await synthesizeSpeech(text, signal));
}

export class SpeechQueue {
  private chain: Promise<void> = Promise.resolve();
  private controller = new AbortController();
  private current: HTMLAudioElement | null = null;
  private urls: string[] = [];

  push(text: string): void {
    const { signal } = this.controller;

    const pending = synthesize(text, signal).catch(() => null);
    this.chain = this.chain
      .then(async () => {
        const url = await pending;
        if (!url || signal.aborted) return;
        this.urls.push(url);
        await this.play(url, signal);
      })
      .catch(() => {});
  }

  private play(url: string, signal: AbortSignal): Promise<void> {
    return new Promise((resolve) => {
      const audio = new Audio(url);
      this.current = audio;
      const done = () => {
        audio.onended = null;
        audio.onerror = null;
        resolve();
      };
      audio.onended = done;
      audio.onerror = done;
      if (signal.aborted) return done();
      signal.addEventListener("abort", done, { once: true });

      audio.play().catch(done);
    });
  }

  stop(): void {
    this.controller.abort();
    if (this.current) {
      this.current.pause();
      this.current = null;
    }
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls = [];
    this.chain = Promise.resolve();
  }
}
