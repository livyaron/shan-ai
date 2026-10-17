import { AlertTriangle } from "lucide-react";
import { bulletText, inlineParts, parseSummary } from "@/lib/aiSummary";

// Lays the dashboard AI summary out as one card per section. The whole reply
// is always shown — nothing here clips, collapses or scrolls inside a box.

const TONES: Record<string, string> = {
  "📈": "from-info/15 border-info/30",
  "⚠️": "from-destructive/15 border-destructive/30",
  "🛠️": "from-success/15 border-success/30",
  "📌": "from-warning/15 border-warning/30",
  "💡": "from-accent/15 border-accent/30",
  "🎯": "from-primary/15 border-primary/40",
};

const Inline = ({ text }: { text: string }) => (
  <>
    {inlineParts(text).map((p, i) => (p.bold ? <strong key={i} className="font-semibold text-foreground">{p.text}</strong> : <span key={i}>{p.text}</span>))}
  </>
);

interface Props {
  text: string;
  streaming: boolean;
}

const AISummaryView = ({ text, streaming }: Props) => {
  const { sections, confidence, incomplete } = parseSummary(text);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {sections.map((s, i) => {
          const tone = TONES[s.icon] || "from-muted border-border";
          const wide = s.icon === "🎯" || !s.title || sections.length === 1;
          return (
            <section
              key={i}
              className={`rounded-2xl border bg-gradient-to-bl to-transparent p-4 ${tone} ${wide ? "md:col-span-2" : ""} animate-in fade-in slide-in-from-bottom-2 duration-500`}
            >
              {s.title && (
                <h3 className="flex items-center gap-2 font-heading font-semibold text-foreground mb-2">
                  {s.icon && <span className="text-lg leading-none">{s.icon}</span>}
                  {s.title}
                </h3>
              )}
              <div className="space-y-1.5 text-sm leading-relaxed text-foreground/85">
                {s.lines.map((line, j) => {
                  const b = bulletText(line);
                  const num = line.match(/^(\d{1,2})[.)]\s/)?.[1];
                  return b !== null ? (
                    <div key={j} className="flex gap-2">
                      {num ? (
                        <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-foreground/10 text-[11px] font-bold">{num}</span>
                      ) : (
                        <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-50" />
                      )}
                      <p><Inline text={b} /></p>
                    </div>
                  ) : (
                    <p key={j}><Inline text={line} /></p>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      {streaming && <span className="inline-block w-1.5 h-4 bg-accent animate-pulse rounded-sm" />}

      {!streaming && confidence !== null && (
        <div className="flex items-center gap-3 rounded-xl bg-muted/50 px-4 py-2.5">
          <span className="text-xs text-muted-foreground whitespace-nowrap">ציון ביטחון של המודל</span>
          <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
            <div className="h-full rounded-full bg-gradient-to-l from-success to-info transition-all duration-700" style={{ width: `${confidence}%` }} />
          </div>
          <span className="text-sm font-bold tabular-nums">{confidence}%</span>
        </div>
      )}

      {!streaming && incomplete && (
        <div className="flex items-center gap-2 rounded-xl border border-warning/40 bg-warning/10 px-4 py-2.5 text-xs text-foreground">
          <AlertTriangle className="w-4 h-4 text-warning shrink-0" />
          ייתכן שהתשובה לא הושלמה (חסר ציון הביטחון שבסופה) — לחץ "רענן ניתוח" לניסיון נוסף.
        </div>
      )}
    </div>
  );
};

export default AISummaryView;
