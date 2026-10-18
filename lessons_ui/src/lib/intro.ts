/**
 * The phone app's opening (PLAN-lessons-mobile.md, owner decisions 2026-10-18):
 * the sketch of a night job in the yard draws itself, then "מערכת לקחים —
 * רושמים לקחים, סוגרים מעגל". It plays once a day; every other launch shows the
 * finished picture while the data loads (it replaces the spinner).
 */

export const INTRO_KEY = "lessons.intro.lastPlayed";
/** How long the animation runs before the app takes over (ms) — matches index.css. */
export const INTRO_MS = 3800;

export const INTRO_TEXT = {
  title: "מערכת לקחים",
  slogan: "רושמים לקחים —",
  punchline: "סוגרים מעגל",
  division: "אגף פרויקטי השנאה",
} as const;

/** Local calendar day — a launch at 01:00 is not "yesterday" in UTC terms. */
const localDay = (now: Date): string =>
  `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

interface Env {
  now?: Date;
  storage?: Pick<Storage, "getItem" | "setItem"> | null;
  reducedMotion?: boolean;
}

/**
 * Play the animation? Once per local day, never for "reduce motion", and never
 * when the phone cannot remember it played (private mode would replay it on
 * every launch — a still picture is the polite fallback).
 */
export const shouldPlayIntro = ({ now = new Date(), storage, reducedMotion = false }: Env = {}): boolean => {
  if (reducedMotion || !storage) return false;
  try {
    return storage.getItem(INTRO_KEY) !== localDay(now);
  } catch {
    return false;
  }
};

export const markIntroPlayed = ({ now = new Date(), storage }: Env = {}): void => {
  try {
    storage?.setItem(INTRO_KEY, localDay(now));
  } catch {
    /* nothing to remember with */
  }
};

/** The browser's own environment, for the component. */
export const browserEnv = (): Env => {
  let storage: Env["storage"] = null;
  try {
    storage = window.localStorage;
  } catch {
    storage = null;
  }
  let reducedMotion = false;
  try {
    reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  } catch {
    reducedMotion = false;
  }
  return { storage, reducedMotion };
};
