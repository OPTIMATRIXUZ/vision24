import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(import.meta.dirname, "globals.css"), "utf8");

function token(name: string): string {
  const start = CSS.indexOf(":root");
  const root = CSS.slice(start, CSS.indexOf("\n}", start));
  const found = root.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!found) throw new Error(`${name} is missing from :root, or is no longer a plain hex`);
  return found[1].toLowerCase();
}

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return (
    0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)
  );
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const WHITE = "#ffffff";
const BODY = "#fafafa";

describe("colour tokens", () => {
  it.each([
    ["--ink-muted", WHITE],
    ["--ink-muted", BODY],
    ["--ink-faint", WHITE],
    ["--ink-faint", BODY],
    ["--danger-ink", WHITE],
  ])("%s reaches AA on %s", (name, background) => {
    expect(contrast(token(name), background)).toBeGreaterThanOrEqual(4.5);
  });

  it("muted text on a chip reaches AA", () => {
    expect(contrast(token("--ink-muted"), token("--chip"))).toBeGreaterThanOrEqual(4.5);
  });

  it("the focus ring is visible against both surfaces", () => {
    for (const background of [WHITE, BODY]) {
      expect(contrast(token("--ring"), background)).toBeGreaterThanOrEqual(3);
    }
  });

  it("keeps faint text lighter than muted text", () => {
    expect(luminance(token("--ink-faint"))).toBeGreaterThan(luminance(token("--ink-muted")));
  });

  it("does not draw the global focus outline at partial opacity", () => {
    const declarations = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(declarations).toContain("outline-ring");
    expect(declarations).not.toContain("outline-ring/");
  });
});
