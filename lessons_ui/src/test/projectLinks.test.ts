import { describe, expect, it } from "vitest";
import { defaultPick, filterLinkRows, managerNeedsAnswer, heldElsewhere, manualOptions, masterLabel, rowState, visibleManagers, type LinkRow } from "@/lib/projectLinks";

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
  const rows = [
    row(1, "חדרה", { identifier: "X", issues: [] }),
    row(2, "עכו", { issues: ["undecided"] }),
    row(3, "חיפה", { decision: "local", manager_name: "דנה", issues: [] }),
    row(4, "נתניה", { identifier: "Y", issues: ["manager_differs"] }),
  ];

  it("the work list (default) shows only rows with an open issue, undecided first", () => {
    expect(filterLinkRows(rows, "", "pending").map((r) => r.id)).toEqual([2, 4]);
  });

  it("a handled row never shows in the work list, only in the control view", () => {
    expect(filterLinkRows(rows, "", "handled").map((r) => r.id)).toEqual([1, 3]);
  });

  it("searches name and manager within the view", () => {
    expect(filterLinkRows(rows, "דנה", "handled").map((r) => r.id)).toEqual([3]);
    expect(filterLinkRows(rows, " נתניה ", "pending").map((r) => r.id)).toEqual([4]);
    expect(filterLinkRows(rows, "חדרה", "pending")).toEqual([]);
  });
});

describe("visibleManagers", () => {
  const m = (name: string, problem: "alias_unlinked" | null) =>
    ({ name, projects: 1, user_id: null, username: null, profile_id: null, problem, hint_user_id: null });
  it("lists only the names still to link in the work list", () => {
    const rows = [m("א", "alias_unlinked"), m("ב", null)];
    expect(visibleManagers(rows, "pending").map((r) => r.name)).toEqual(["א"]);
    expect(visibleManagers(rows, "handled").map((r) => r.name)).toEqual(["ב"]);
  });
});

describe("manualOptions", () => {
  it("lists every file project and marks the ones another lessons project holds", () => {
    const masters = [
      { identifier: "A", name: "א", manager: null, stage: null, project_type: null, linked_to: null },
      { identifier: "B", name: "ב", manager: null, stage: null, project_type: null, linked_to: 5, linked_name: "פרויקט ה" },
      { identifier: "C", name: "ג", manager: null, stage: null, project_type: null, linked_to: 7, linked_name: "פרויקט ז" },
    ];
    const opts = manualOptions(masters, 5);
    expect(opts.map((o) => o.value)).toEqual(["A", "B", "C"]);
    expect(opts[1].label).toBe("ב");                          // held by this very row
    expect(opts[2].label).toContain('מקושר ל"פרויקט ז"');
    expect(heldElsewhere(masters[2], 5)).toBe(true);
    expect(heldElsewhere(masters[1], 5)).toBe(false);
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
