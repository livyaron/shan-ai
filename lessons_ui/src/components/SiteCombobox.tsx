import { useState, useEffect, useMemo } from "react";
import { Check, ChevronsUpDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { cn } from "@/lib/utils";
import { supabase } from "@/integrations/supabase/client";

interface SiteOption {
  id: number;
  label: string;
  value: string;
  isActive: boolean;
}

interface SiteComboboxProps {
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
}

/** Searchable autocomplete for site selection with partial matching */
const SiteCombobox = ({ value, onValueChange, disabled }: SiteComboboxProps) => {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<SiteOption[]>([]);
  const [search, setSearch] = useState("");

  useEffect(() => {
    const load = async () => {
      // Get site field config id
      const { data: configs } = await supabase
        .from("form_field_configs")
        .select("id")
        .eq("form_type", "project")
        .eq("field_key", "site")
        .limit(1);

      if (!configs || configs.length === 0) return;

      const configId = configs[0].id;
      const { data: opts } = await supabase
        .from("form_field_options")
        .select("*")
        .eq("field_config_id", configId)
        .eq("is_active", true)
        .order("sort_order");

      if (opts) {
        setOptions(opts.map((o: any) => ({
          id: o.id,
          label: o.label,
          value: o.value,
          isActive: o.is_active,
        })));
      }
    };
    load();
  }, []);

  // Partial match filter — case insensitive, trimmed, matches anywhere in string
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return options;
    return options.filter((o) => o.label.toLowerCase().includes(term));
  }, [options, search]);

  const selectedLabel = options.find((o) => o.value === value)?.label;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="w-full justify-between mt-1.5 font-normal"
        >
          {selectedLabel || "בחר אתר..."}
          <ChevronsUpDown className="mr-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="חפש אתר..."
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>לא נמצאו אתרים</CommandEmpty>
            <CommandGroup>
              {filtered.map((option) => (
                <CommandItem
                  key={option.id}
                  value={option.value}
                  onSelect={() => {
                    onValueChange(option.value === value ? "" : option.value);
                    setOpen(false);
                    setSearch("");
                  }}
                >
                  <Check
                    className={cn(
                      "ml-2 h-4 w-4",
                      value === option.value ? "opacity-100" : "opacity-0"
                    )}
                  />
                  {option.label}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
};

export default SiteCombobox;
