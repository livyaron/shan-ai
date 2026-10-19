import { describe, expect, it } from "vitest";
import { isLinked, missingLabel, projectProblems } from "@/lib/projectProblems";

describe("projectProblems", () => {
  it("flags what the master file cannot know", () => {
    expect(projectProblems({ stationTypeSet: false, site: undefined })).toEqual(["station_type_unset", "no_site"]);
    expect(projectProblems({ stationTypeSet: true, site: "  " })).toEqual(["no_site"]);
    expect(projectProblems({ stationTypeSet: true, site: "חדרה" })).toEqual([]);
  });

  it("never asks for equipment (user decision 2026-10-19)", () => {
    expect(projectProblems({ stationTypeSet: true, site: "חדרה", equipmentIds: [] } as never)).toEqual([]);
  });

  it("names the missing fields", () => {
    expect(missingLabel(["station_type_unset", "no_site"])).toBe("חסר: סוג תחנה, אתר");
    expect(missingLabel([])).toBe("");
  });

  it("knows a linked project", () => {
    expect(isLinked({ shanIdentifier: "WBE-1" })).toBe(true);
    expect(isLinked({ shanIdentifier: undefined })).toBe(false);
  });
});
