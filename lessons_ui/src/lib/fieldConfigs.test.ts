import { describe, expect, it, vi } from "vitest";

vi.mock("@/integrations/supabase/client", () => ({ supabase: {} }));

import { findField, labelsFor, type FieldConfigRow } from "./fieldConfigs";

const row = (form_type: string, field_key: string, display_name: string, id = 1): FieldConfigRow =>
  ({ id, form_type, field_key, display_name, is_visible: true, is_required: false, sort_order: id });

const rows = [row("project", "site", "אתר", 1), row("lesson", "site", "אתר האירוע", 2), row("lesson", "title", "כותרת", 3)];

describe("field settings shared by every caller", () => {
  it("finds a field by form and key — the same key on another form is a different field", () => {
    expect(findField(rows, "project", "site")?.id).toBe(1);
    expect(findField(rows, "lesson", "site")?.id).toBe(2);
    expect(findField(rows, "project", "title")).toBeUndefined();
  });

  it("labels are taken from one form only", () => {
    expect(labelsFor(rows, "lesson")).toEqual({ site: "אתר האירוע", title: "כותרת" });
    expect(labelsFor(rows, "project")).toEqual({ site: "אתר" });
    expect(labelsFor([], "lesson")).toEqual({});
  });
});
