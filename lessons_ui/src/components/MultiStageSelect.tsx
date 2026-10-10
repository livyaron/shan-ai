import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { PROJECT_STAGES } from "@/context/UserContext";
import { cn } from "@/lib/utils";

interface MultiStageSelectProps {
  value: number[];
  onChange: (next: number[]) => void;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
}

const MultiStageSelect = ({ value, onChange, disabled, className, placeholder = "בחר שלבים" }: MultiStageSelectProps) => {
  const [open, setOpen] = useState(false);

  const toggle = (idx: number) => {
    const exists = value.includes(idx);
    if (exists) {
      // Enforce minimum 1 — block removing the last
      if (value.length === 1) return;
      onChange(value.filter((v) => v !== idx).sort((a, b) => a - b));
    } else {
      onChange([...value, idx].sort((a, b) => a - b));
    }
  };

  const sortedSelected = [...value].sort((a, b) => a - b);
  const triggerLabel =
    sortedSelected.length === 0
      ? placeholder
      : sortedSelected.length === 1
      ? PROJECT_STAGES[sortedSelected[0]]
      : `נבחרו ${sortedSelected.length} שלבים`;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn("w-full justify-between font-normal", className)}
        >
          <span className="truncate">{triggerLabel}</span>
          <ChevronDown className="w-4 h-4 opacity-50 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent dir="rtl" className="w-[var(--radix-popover-trigger-width)] p-2" align="start">
        <div className="max-h-64 overflow-y-auto space-y-1">
          {PROJECT_STAGES.map((stage, idx) => {
            const checked = value.includes(idx);
            const isLastSelected = checked && value.length === 1;
            return (
              <label
                key={idx}
                className={cn(
                  "flex items-center gap-2 px-2 py-1.5 rounded-md text-sm",
                  isLastSelected ? "cursor-not-allowed opacity-70" : "cursor-pointer hover:bg-muted"
                )}
                title={isLastSelected ? "חובה לבחור לפחות שלב אחד" : undefined}
              >
                <Checkbox
                  checked={checked}
                  disabled={isLastSelected}
                  onCheckedChange={() => toggle(idx)}
                />
                <span className="flex-1">{stage}</span>
                {checked && <Check className="w-3.5 h-3.5 text-accent" />}
              </label>
            );
          })}
        </div>
        <p className="text-[11px] text-muted-foreground mt-2 px-2">חובה לבחור לפחות שלב אחד</p>
      </PopoverContent>
    </Popover>
  );
};

export default MultiStageSelect;
