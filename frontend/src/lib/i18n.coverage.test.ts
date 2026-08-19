import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(import.meta.dirname, "..");
const ALLOWED = new Set([
  "Vision24",
  "CCTV",
  "RTSP",

  "Asia/Tashkent",
  "rtsp://user:password@192.168.1.64:554/stream1",
]);

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    if (!/\.tsx$/.test(entry.name) || /\.test\.tsx$/.test(entry.name)) return [];
    return [path];
  });
}

const PROSE = /[A-Za-z]{2,}(?:[ ,.'’—-]+[A-Za-z]{2,})+/;

function offenders(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const found: string[] = [];

  for (const match of text.matchAll(/\b(?:placeholder|title|aria-label|alt|label)="([^"]+)"/g)) {
    const value = match[1];
    if (!ALLOWED.has(value) && PROSE.test(value)) found.push(`${match[0]}`);
  }

  for (const match of text.matchAll(/>\s*([A-Za-z][^<>{}()[\];=*`"]{3,})\s*</g)) {
    const value = match[1].trim().replace(/\s+/g, " ");
    if (!ALLOWED.has(value) && PROSE.test(value)) found.push(`>${value}<`);
  }

  return found;
}

describe("message catalog coverage", () => {
  const files = [...sources(join(ROOT, "app")), ...sources(join(ROOT, "components"))];

  it("scans a meaningful number of files", () => {
    expect(files.length).toBeGreaterThan(15);
  });

  it.each(files.map((f) => [f.slice(ROOT.length + 1), f]))(
    "%s renders no untranslated prose",
    (_name, file) => {
      expect(offenders(file)).toEqual([]);
    },
  );
});
