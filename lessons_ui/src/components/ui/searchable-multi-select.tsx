import { useMemo, useState, ReactNode } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { cn } from "@/lib/utils";

export interface MultiOption {
  value: string;
  label: string;
  hint?: ReactNode;
}

interface SearchableMultiSelectProps {
  value: string[];
  onChange: (next: string[]) => void;
  options: MultiOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  disabled?: boolean;
  className?: string;
  /** Minimum required selections; blocks removing the last when reached. */
  minSelected?: number;
  /** Show "select all" / "clear" actions. */
  showBulkActions?: boolean;
  /** Show selected items as removable badges in the trigger. Default: false (shows count). */
  showSelectedBadges?: boolean;
  searchable?: boolean;
  dir?: "rtl" | "ltr";
}

const SearchableMultiSelect = ({
  value,
  onChange,
  options,
  placeholder = "בחר...",
  searchPlaceholder = "חיפוש...",
  emptyMessage = "לא נמצאו תוצאות",
  disabled,
  className,
  minSelected = 0,
  showBulkActions = false,
  showSelectedBadges = false,
  searchable,
  dir = "rtl",
}: SearchableMultiSelectProps) => {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const showSearch = searchable ?? options.length > 4;

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return options;
    return options.filter((o) => o.label.toLowerCase().includes(term));
  }, [options, search]);

  const toggle = (val: string) => {
    if (value.includes(val)) {
      if (value.length <= minSelected) return;
      onChange(value.filter((v) => v !== val));
    } else {
      onChange([...value, val]);
    }
  };

  const selectAll = () => onChange(options.map((o) => o.value));
  const clearAll = () => {
    if (minSelected > 0 && options.length > 0) {
      onChange([options[0].value]);
    } else {
      onChange([]);
    }
  };

  const triggerLabel =
    value.length === 0
      ? placeholder
      : value.length === 1
      ? options.find((o) => o.value === value[0])?.label || placeholder
      : `נבחרו ${value.length}`;

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setSearch(""); }}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            "flex min-h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm font-normal ring-offset-background hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
            value.length === 0 && "text-muted-foreground",
            className
          )}
        >
          {showSelectedBadges && value.length > 0 ? (
            <div className="flex flex-wrap gap-1 flex-1 text-start">
              {value.map((v) => {
                const opt = options.find((o) => o.value === v);
                if (!opt) return null;
                return (
                  <Badge key={v} variant="secondary" className="gap-1 pr-1">
                    {opt.label}
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(e) => { e.stopPropagation(); toggle(v); }}
                      className="hover:bg-muted-foreground/20 rounded-sm p-0.5"
                    >
                      <X className="h-3 w-3" />
                    </span>
                  </Badge>
                );
              })}
            </div>
          ) : (
            <span className="truncate text-start">{triggerLabel}</span>
          )}
          <ChevronDown className="h-4 w-4 opacity-50 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        dir={dir}
        className="w-[var(--radix-popover-trigger-width)] p-0"
        align="start"
      >
        <Command shouldFilter={false}>
          {showSearch && (
            <CommandInput
              placeholder={searchPlaceholder}
              value={search}
              onValueChange={setSearch}
            />
          )}
          {showBulkActions && (
            <div className="flex items-center justify-between gap-2 px-2 py-1.5 border-b text-xs">
              <button type="button" onClick={selectAll} className="text-accent hover:underline">
                בחר הכל
              </button>
              <button type="button" onClick={clearAll} className="text-muted-foreground hover:underline">
                נקה
              </button>
            </div>
          )}
          <CommandList>
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            <CommandGroup>
              {filtered.map((option) => {
                const checked = value.includes(option.value);
                const isLast = checked && value.length <= minSelected;
                return (
                  <CommandItem
                    key={option.value}
                    value={option.value}
                    onSelect={() => toggle(option.value)}
                    disabled={isLast}
                    className={cn(isLast && "opacity-70 cursor-not-allowed")}
                  >
                    <Checkbox checked={checked} className="ml-2 pointer-events-none" />
                    <span className="flex-1">{option.label}</span>
                    {option.hint && (
                      <span className="text-xs text-muted-foreground mr-2">{option.hint}</span>
                    )}
                    {checked && <Check className="h-3.5 w-3.5 text-accent" />}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
};

export default SearchableMultiSelect;
