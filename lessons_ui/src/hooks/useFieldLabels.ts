import { useState, useEffect } from "react";
import { supabase } from "@/integrations/supabase/client";

/** Map of field_key → display_name from form_field_configs */
type FieldLabels = Record<string, string>;

/** Default fallback labels for lesson form fields */
const LESSON_DEFAULTS: FieldLabels = {
  title: "כותרת הלקח",
  description: "תיאור מפורט",
  recommendation: "המלצה",
  project_name: "פרויקט מקור",
  stage: "שלב",
  category: "קטגוריה",
  risk: "רמת סיכון",
  professional_domain: "תחום מקצועי / מגזר",
  event_date: "תאריך האירוע",
  impact_schedule_delay: "השפעה על לו״ז – הערכת עיכוב (ימים)",
  impact_budget_cost: "השפעה על תקציב – הערכת עלות (₪)",
  impact_quality_desc: "השפעה על איכות",
  equipment_ids: "ציוד / נושאים",
  assigned_to: "אחראי ליישום",
  workflow_status: "WF – סטטוס טיפול",
  target_date: "תאריך יעד",
};

/**
 * Fetches dynamic field labels from form_field_configs for a given form type.
 * Returns a getter function that falls back to hardcoded defaults if no DB value exists.
 */
export function useFieldLabels(formType: "lesson" | "project") {
  const [labels, setLabels] = useState<FieldLabels>({});

  useEffect(() => {
    const load = async () => {
      const { data } = await supabase
        .from("form_field_configs")
        .select("field_key, display_name")
        .eq("form_type", formType);

      if (data) {
        const map: FieldLabels = {};
        for (const row of data) {
          map[row.field_key] = row.display_name;
        }
        setLabels(map);
      }
    };
    load();
  }, [formType]);

  /** Get the display label for a field key, with fallback */
  const getLabel = (fieldKey: string, fallback?: string): string => {
    return labels[fieldKey] || fallback || (formType === "lesson" ? LESSON_DEFAULTS[fieldKey] : undefined) || fieldKey;
  };

  return { getLabel, labels };
}
