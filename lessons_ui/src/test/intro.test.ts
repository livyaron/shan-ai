import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { INTRO_KEY, INTRO_MS, INTRO_TEXT, markIntroPlayed, shouldPlayIntro } from "@/lib/intro";

const memory = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
};

describe("the opening plays once a day", () => {
  it("plays on the first start of the day, then not again that day", () => {
    const storage = memory();
    const morning = new Date(2026, 9, 18, 7, 0);
    expect(shouldPlayIntro({ now: morning, storage })).toBe(true);
    markIntroPlayed({ now: morning, storage });
    expect(shouldPlayIntro({ now: new Date(2026, 9, 18, 23, 59), storage })).toBe(false);
    expect(shouldPlayIntro({ now: new Date(2026, 9, 19, 0, 1), storage })).toBe(true);
  });

  it("counts days by the phone's clock, not UTC", () => {
    const storage = memory();
    markIntroPlayed({ now: new Date(2026, 9, 18, 1, 30), storage });
    expect(storage.getItem(INTRO_KEY)).toBe("2026-10-18");
  });

  it("never plays for 'reduce motion', or when the phone cannot remember it played", () => {
    expect(shouldPlayIntro({ storage: memory(), reducedMotion: true })).toBe(false);
    expect(shouldPlayIntro({ storage: null })).toBe(false);
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(shouldPlayIntro({ storage: broken })).toBe(false);
    expect(() => markIntroPlayed({ storage: broken })).not.toThrow();
  });
});

describe("the opening's content and assets", () => {
  const css = readFileSync("src/index.css", "utf8");
  const vite = readFileSync("vite.config.ts", "utf8");

  it("carries the owner's chosen text (2026-10-18)", () => {
    expect(INTRO_TEXT).toEqual({ title: "מערכת לקחים", slogan: "רושמים לקחים —", punchline: "סוגרים מעגל", division: "אגף פרויקטי השנאה" });
  });

  it("the timer covers the last animation in the CSS", () => {
    // the ring starts at 2.7s and runs .6s; the division fades in at 3s for .5s
    expect(INTRO_MS).toBeGreaterThanOrEqual(3500);
    expect(css).toContain("animation: intro-ring .6s ease-in-out 2.7s forwards");
    expect(css).toContain(".intro-division { animation-delay: 3s; }");
  });

  it("works offline: images and the title font are precached by the service worker", () => {
    expect(vite).toMatch(/globPatterns: \["\*\*\/\*\.\{[^}]*woff2[^}]*webp[^}]*\}"\]/);
    expect(css).toContain('url("/lessons/fonts/karantina-700-hebrew.woff2")');
  });

  it("hold mode and reduce motion show the finished picture at once", () => {
    expect(css).toContain('.intro[data-mode="hold"] *');
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  });
});
