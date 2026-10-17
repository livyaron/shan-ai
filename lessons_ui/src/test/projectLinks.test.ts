import { describe, expect, it } from "vitest";
import { defaultPick, filterLinkRows, managerNeedsAnswer, freeMasters, masterLabel, rowState, type LinkRow } from "@/lib/projectLinks";

const row = (id: number, name: string, extra: Partial<LinkRow> = {}): LinkRow => ({
  id, name, site: null, lessons_count: 0, manager_name: null, identifier: null, linked: null, manager_check: null,
  linked_missing: false, decision: null, suggestions: [], ...extra,
});

describe("rowState", () => {
  it("linked beats local", () => {
    expect(rowState(row(1, "א", { identifier: "X", decision: "local" }))).toBe("linked");
    expect(rowState(row(2, "ב", { decision: "local" }))).toBe("local");
    expect(rowState(row(3, "ג"))).toBe("open");
  });
});

describe("filterLinkRows", () => {
  const rows = [row(1, "חדרה", { identifier: "X" }), row(2, "עכו"), row(3, "חיפה", { decision: "local", manager_name: "דנה" })];

  it("puts the open rows first", () => {
    expect(filterLinkRows(rows, "", false).map((r) => r.id)[0]).toBe(2);
  });

  it("keeps only the open rows when asked", () => {
    expect(filterLinkRows(rows, "", true).map((r) => r.id)).toEqual([2]);
  });

  it("searches name and manager", () => {
    expect(filterLinkRows(rows, "דנה", false).map((r) => r.id)).toEqual([3]);
    expect(filterLinkRows(rows, " חדרה ", false).map((r) => r.id)).toEqual([1]);
  });
});

describe("freeMasters", () => {
  it("hides file projects linked to another lessons project", () => {
    const masters = [
      { identifier: "A", name: "א", manager: null, stage: null, project_type: null, linked_to: null },
      { identifier: "B", name: "ב", manager: null, stage: null, project_type: null, linked_to: 5 },
      { identifier: "C", name: "ג", manager: null, stage: null, project_type: null, linked_to: 7 },
    ];
    expect(freeMasters(masters, 5).map((m) => m.identifier)).toEqual(["A", "B"]);
  });
});

describe("masterLabel", () => {
  it("names the project, its manager and stage", () => {
    expect(masterLabel({ identifier: "A", name: "חדרה", manager: "דנה", stage: "תכנון", project_type: null }))
      .toBe('חדרה · מנה"פ דנה · תכנון');
    expect(masterLabel({ identifier: "A", name: null, manager: null, stage: null, project_type: null })).toBe("A");
  });
});

describe("defaultPick", () => {
  const sg = (identifier: string, recommended: boolean) => ({
    identifier, name: identifier, manager: null, stage: null, project_type: null,
    score: 50, id_match: false, manager_match: false, recommended,
  });

  it("pre-selects only a recommended suggestion", () => {
    expect(defaultPick(row(1, "א", { suggestions: [sg("A", true), sg("B", false)] }))).toBe("A");
    expect(defaultPick(row(2, "ב", { suggestions: [sg("A", false), sg("B", false)] }))).toBe("");
    expect(defaultPick(row(3, "ג"))).toBe("");
  });
});

describe("managerNeedsAnswer", () => {
  const mc = (status: "match" | "differs" | "unresolved" | "kept" | "no_name") => ({
    status, file_manager: "משה", problem: null, file_profile: null, current: { id: "u1", name: "דנה" }, suggest_user_id: null,
  });

  it("asks only when the file and the module disagree or the file's name is unknown", () => {
    expect(managerNeedsAnswer(row(1, "א", { manager_check: mc("differs") }))).toBe(true);
    expect(managerNeedsAnswer(row(2, "ב", { manager_check: mc("unresolved") }))).toBe(true);
    for (const st of ["match", "kept", "no_name"] as const) {
      expect(managerNeedsAnswer(row(3, "ג", { manager_check: mc(st) }))).toBe(false);
    }
    expect(managerNeedsAnswer(row(4, "ד"))).toBe(false);
  });
});
