import { useEffect, useRef, useState } from "react";
import { SUPABASE_URL } from "@/integrations/supabase/client";

/**
 * Shan-AI's shared top bar, so the module looks and navigates like every
 * other Shan-AI page. The HTML is Shan-AI's own `_navbar.html`, rendered by
 * the server for the signed-in user (GET /lessons/api/_navbar) — one source of
 * truth, never a copy here.
 *
 * Its height is published as `--shan-nav-h`: the module's fixed sidebar and
 * sticky top bar sit under it (the navbar wraps to two rows on narrow screens).
 */
const ShanNavbar = () => {
  const [html, setHtml] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    fetch(`${SUPABASE_URL}/_navbar`, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(`/_navbar ${r.status}`);
        return r.text();
      })
      .then((t) => alive && setHtml(t))
      .catch((e) => {
        console.warn("Shan-AI navbar failed to load:", e);
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    const el = ref.current;
    if (!el) {
      root.style.setProperty("--shan-nav-h", "0px");
      return;
    }
    const publish = () => root.style.setProperty("--shan-nav-h", `${el.offsetHeight}px`);
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.setProperty("--shan-nav-h", "0px");
    };
  }, [html, failed]);

  if (failed) {
    // Never leave the module without a way back to Shan-AI.
    return (
      <div ref={ref} dir="rtl" className="sticky top-0 z-[60] flex items-center justify-between gap-4 px-5 py-3 text-sm"
        style={{ background: "#070b12", borderBottom: "1px solid #1a2d47" }}>
        <a href="/dashboard/" style={{ color: "#00d4ff", fontWeight: 600 }}>SHAN-AI / DSS ← חזרה לדשבורד</a>
        <a href="/logout" style={{ color: "#94a3b8" }}>🚪 יציאה</a>
      </div>
    );
  }
  if (!html) return null;
  // Server-rendered by Shan-AI from its own template (every value escaped by Jinja).
  return <div ref={ref} className="sticky top-0 z-[60]" dangerouslySetInnerHTML={{ __html: html }} />;
};

export default ShanNavbar;
