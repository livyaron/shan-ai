import { cn } from "@/lib/utils";

interface Option<T extends string | number> {
  value: T;
  label: string;
}

interface Props<T extends string | number> {
  options: Option<T>[];
  /** One value, or several when `multiple`. */
  value: T | T[];
  onChange: (value: T) => void;
  multiple?: boolean;
  label?: string;
}

/** Big tap targets instead of a dropdown — one tap, no keyboard (PLAN-lessons-mobile.md M1). */
const ChoiceChips = <T extends string | number>({ options, value, onChange, multiple, label }: Props<T>) => {
  const selected = (v: T) => (Array.isArray(value) ? value.includes(v) : value === v);
  return (
    <div role={multiple ? "group" : "radiogroup"} aria-label={label} className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role={multiple ? "checkbox" : "radio"}
          aria-checked={selected(o.value)}
          onClick={() => onChange(o.value)}
          className={cn(
            "min-h-11 px-4 py-2 rounded-full border text-sm transition-colors",
            selected(o.value)
              ? "bg-primary text-primary-foreground border-primary"
              : "bg-card border-border active:bg-muted",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
};

export default ChoiceChips;
