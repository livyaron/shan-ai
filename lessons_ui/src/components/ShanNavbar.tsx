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
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    fetch(`${SUPABASE_URL}/_navbar`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.text() : null))
      .then((t) => alive && setHtml(t))
      .catch(() => alive && setHtml(null));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    const el = ref.current;
    if (!el || !html) {
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
  }, [html]);

  if (!html) return null;
  // Server-rendered by Shan-AI from its own template (every value escaped by Jinja).
  return <div ref={ref} className="sticky top-0 z-[60]" dangerouslySetInnerHTML={{ __html: html }} />;
};

export default ShanNavbar;
