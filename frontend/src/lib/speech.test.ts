import { describe, expect, it } from "vitest";

import { takeSentences } from "@/lib/speech";

describe("takeSentences", () => {
  it("cuts on a period when the next sentence clearly starts", () => {
    const { sentences, rest } = takeSentences("Привет. Как дела");
    expect(sentences).toEqual(["Привет."]);
    expect(rest).toBe("Как дела");
  });

  it("treats a Cyrillic capital as a sentence start", () => {
    const { sentences } = takeSentences("Первое. Второе предложение тут");
    expect(sentences).toEqual(["Первое."]);
  });

  it("does not cut at an abbreviation followed by lowercase", () => {
    const { sentences } = takeSentences("Было 9 чел. в 15:11 сегодня. Потом ушли");
    expect(sentences).toEqual(["Было 9 чел. в 15:11 сегодня."]);
  });

  it("holds a trailing period until more text decides it", () => {
    const { sentences, rest } = takeSentences("30 сек. ");
    expect(sentences).toEqual([]);
    expect(rest).toBe("30 сек. ");
  });

  it("cuts on a newline regardless of what follows", () => {
    const { sentences, rest } = takeSentences("Первая строка\nвторая");
    expect(sentences).toEqual(["Первая строка"]);
    expect(rest).toBe("вторая");
  });

  it("skips fragments with no letters", () => {
    const { sentences, rest } = takeSentences("... Привет");
    expect(sentences).toEqual([]);
    expect(rest).toBe("Привет");
  });

  it("keeps a closing quote with the sentence it ends", () => {
    const { sentences } = takeSentences('Он сказал "Да." Потом ушёл');
    expect(sentences).toEqual(['Он сказал "Да."']);
  });

  it("returns everything as rest when no boundary is complete", () => {
    const { sentences, rest } = takeSentences("Готово.");
    expect(sentences).toEqual([]);
    expect(rest).toBe("Готово.");
  });

  it("pulls several sentences out of one buffer", () => {
    const { sentences, rest } = takeSentences("Раз. Два! Три? Четыре");
    expect(sentences).toEqual(["Раз.", "Два!", "Три?"]);
    expect(rest).toBe("Четыре");
  });

  it("handles an empty buffer", () => {
    expect(takeSentences("")).toEqual({ sentences: [], rest: "" });
  });

  it("works the same in English", () => {
    const { sentences, rest } = takeSentences("Hello there. Next one");
    expect(sentences).toEqual(["Hello there."]);
    expect(rest).toBe("Next one");
  });
});
