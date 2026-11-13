import { supabase } from "@/integrations/supabase/client";
import { sharedLoad } from "@/lib/sharedLoad";

/**
 * The form field settings (labels, visible, required), fetched ONCE for every
 * caller on the screen. A project page used to ask for the same small table
 * three or four times — the labels hook, the site field, the site picker —
 * each with its own filter. The table is a few dozen rows: one unfiltered read
 * serves them all. FormFieldManager calls `invalidate()` after every change.
 */
export interface FieldConfigRow {
  id: number;
  form_type: string;
  field_key: string;
  display_name: string;
  is_visible: boolean;
  is_required: boolean;
  sort_order: number;
}

export const fieldConfigs = sharedLoad(async (): Promise<FieldConfigRow[]> => {
  const { data, error } = await supabase.from("form_field_configs").select("*");
  if (error) throw error;          // a failed read is not kept — the next caller tries again
  return (data ?? []) as FieldConfigRow[];
}, 10_000);

export function findField(rows: FieldConfigRow[], formType: string, fieldKey: string): FieldConfigRow | undefined {
  return rows.find((r) => r.form_type === formType && r.field_key === fieldKey);
}

/** field_key → display_name for one form. */
export function labelsFor(rows: FieldConfigRow[], formType: string): Record<string, string> {
  const map: Record<string, string> = {};
  for (const r of rows) if (r.form_type === formType) map[r.field_key] = r.display_name;
  return map;
}
