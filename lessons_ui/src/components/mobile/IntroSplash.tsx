import { useEffect } from "react";
import { INTRO_MS, INTRO_TEXT } from "@/lib/intro";

/**
 * The opening screen (PLAN-lessons-mobile.md). "play" runs the animation in
 * index.css (.intro-*) and calls onDone after INTRO_MS or on a tap; "hold" shows
 * the finished picture at once — the loading screen of every other launch.
 * Images: public/intro/ink.webp (navy lines + halftone) and orange.webp (the
 * people and the lift), made from a field photo — brand names erased.
 */
const IntroSplash = ({ mode, onDone, loading = false }: { mode: "play" | "hold"; onDone?: () => void; loading?: boolean }) => {
  useEffect(() => {
    if (mode !== "play" || !onDone) return;
    const t = window.setTimeout(onDone, INTRO_MS);
    return () => window.clearTimeout(t);
  }, [mode, onDone]);

  const base = import.meta.env.BASE_URL;
  return (
    <div
      dir="rtl"
      data-mode={mode}
      className="intro fixed inset-0 z-[100] flex flex-col bg-background overflow-hidden"
      onClick={mode === "play" ? onDone : undefined}
      role="img"
      aria-label={`${INTRO_TEXT.title} — ${INTRO_TEXT.slogan} ${INTRO_TEXT.punchline}`}
    >
      <div className="intro-art relative w-full shrink-0">
        <img src={`${base}intro/ink.webp`} alt="" className="intro-ink absolute inset-0 w-full h-full object-cover" />
        <span className="intro-pen absolute inset-x-0 h-1 bg-accent" />
        <img src={`${base}intro/orange.webp`} alt="" className="intro-orange absolute inset-0 w-full h-full object-cover" />
        <span className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-b from-transparent to-background" />
      </div>
      <div className="relative -mt-14 flex flex-1 flex-col items-center text-center px-6">
        <h1 className="intro-title font-intro text-primary leading-none text-[4.5rem]">{INTRO_TEXT.title}</h1>
        <span className="intro-bar mt-3 h-1 rounded-full bg-accent" />
        <p className="intro-slogan mt-4 text-lg text-foreground">{INTRO_TEXT.slogan}</p>
        <p className="intro-punch mt-1 flex items-center gap-2 text-2xl font-semibold text-primary">
          {INTRO_TEXT.punchline}
          <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
            <circle className="intro-ring" cx="12" cy="12" r="9" fill="none" stroke="hsl(var(--accent))" strokeWidth="3.5" strokeLinecap="round" transform="rotate(-90 12 12)" />
          </svg>
        </p>
        <p className="intro-division mt-auto mb-8 text-sm tracking-wide text-primary/70">
          {INTRO_TEXT.division}
          {loading && mode === "hold" && <span className="block mt-2 text-xs text-muted-foreground">טוען…</span>}
        </p>
      </div>
    </div>
  );
};

export default IntroSplash;
