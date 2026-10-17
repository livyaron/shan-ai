import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

// A ranked list of horizontal bars. Replaces the vertical recharts bars whose
// -45° Hebrew labels ran into each other and into the plot (user, 2026-10-17):
// here a label owns its own row, is truncated with the full text on hover,
// and never touches the bar.

export interface RankedItem {
  key: string | number;
  label: string;
  value: number;
  lessonIds: number[];
}

interface Props {
  items: RankedItem[];
  onPick: (lessonIds: number[]) => void;
  /** CSS colour of the bar fill. */
  color?: string;
  limit?: number;
  unit?: string;
}

const RankedBars = ({ items, onPick, color = "hsl(var(--primary))", limit = 8, unit = "לקחים" }: Props) => {
  const [showAll, setShowAll] = useState(false);
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-8">אין נתונים</p>;
  }
  const max = Math.max(...items.map((i) => i.value), 1);
  const total = items.reduce((s, i) => s + i.value, 0);
  const shown = showAll ? items : items.slice(0, limit);

  return (
    <div className="space-y-1.5">
      {shown.map((item, idx) => {
        const pct = Math.max(3, Math.round((item.value / max) * 100));
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => onPick(item.lessonIds)}
            title={`${item.label} — ${item.value} ${unit}`}
            className="group w-full grid grid-cols-[1.25rem_1fr_4.5rem] sm:grid-cols-[1.25rem_minmax(7rem,16rem)_1fr_4.5rem] items-center gap-x-3 gap-y-1 rounded-lg px-2 py-2 text-right hover:bg-muted/60 transition-colors"
          >
            <span className="text-[11px] font-semibold text-muted-foreground tabular-nums">{idx + 1}</span>
            <span className="truncate text-sm font-medium text-foreground">{item.label}</span>
            <span className="relative h-3 rounded-full bg-muted overflow-hidden col-start-2 col-span-2 row-start-2 sm:col-start-auto sm:col-span-1 sm:row-start-auto">
              <span
                className="absolute inset-y-0 right-0 rounded-full transition-all duration-500 group-hover:brightness-110"
                style={{ width: `${pct}%`, background: `linear-gradient(to left, ${color}, color-mix(in srgb, ${color} 55%, transparent))` }}
              />
            </span>
            <span className="flex items-baseline justify-end gap-1.5 text-sm font-bold tabular-nums whitespace-nowrap">
              {item.value}
              <span className="text-[11px] font-normal text-muted-foreground">
                {total ? Math.round((item.value / total) * 100) : 0}%
              </span>
            </span>
          </button>
        );
      })}
      {items.length > limit && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="mx-auto mt-2 flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          {showAll ? <>הצג פחות <ChevronUp className="w-3 h-3" /></> : <>הצג את כל {items.length} <ChevronDown className="w-3 h-3" /></>}
        </button>
      )}
    </div>
  );
};

export default RankedBars;
