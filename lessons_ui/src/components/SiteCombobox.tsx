import { useState, useEffect, useMemo } from "react";
import { Check, ChevronsUpDown, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { cn } from "@/lib/utils";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";
import { findSite } from "@/lib/siteName";

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
  /** Offer "add a new site" (admin screens): the typed text, else `suggestedNew`. */
  allowCreate?: boolean;
  suggestedNew?: string;
}

/** Searchable autocomplete for site selection with partial matching */
const SiteCombobox = ({ value, onValueChange, disabled, allowCreate, suggestedNew }: SiteComboboxProps) => {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<SiteOption[]>([]);
  const [search, setSearch] = useState("");
  const [configId, setConfigId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);

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
      setConfigId(configId);
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

  // A new site: what was typed, else the suggestion (the station name). An
  // existing site with the same name is selected instead of duplicated.
  const newName = (search.trim() || suggestedNew || "").trim();
  const existing = newName ? findSite(options, newName) : undefined;
  const createSite = async () => {
    if (!newName || configId === null) return;
    if (existing) {
      onValueChange(existing.value);
      setOpen(false);
      setSearch("");
      return;
    }
    setCreating(true);
    const maxOrder = (await supabase.from("form_field_options").select("sort_order")
      .eq("field_config_id", configId).order("sort_order", { ascending: false }).limit(1)).data?.[0]?.sort_order ?? 0;
    const { data, error } = await supabase.from("form_field_options").insert({
      field_config_id: configId, label: newName, value: newName, sort_order: maxOrder + 1, is_active: true,
    }).select().single();
    setCreating(false);
    if (error || !data) {
      toast({ title: "הוספת האתר נכשלה", description: error?.message, variant: "destructive" });
      return;
    }
    setOptions((prev) => [...prev, { id: data.id, label: data.label, value: data.value, isActive: true }]);
    onValueChange(data.value);
    setOpen(false);
    setSearch("");
    toast({ title: `האתר "${newName}" נוסף ✓` });
  };

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
            {allowCreate && newName && configId !== null && (
              <CommandGroup>
                <CommandItem value={`__new__${newName}`} onSelect={createSite} disabled={creating}>
                  <Plus className="ml-2 h-4 w-4" />
                  {existing ? `בחר את "${existing.label}" (קיים)` : `הוסף אתר חדש: "${newName}"`}
                </CommandItem>
              </CommandGroup>
            )}
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
