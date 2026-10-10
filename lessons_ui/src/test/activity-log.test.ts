import { describe, expect, it } from "vitest";
import { filterActivityLogs, formatActivityDateTime, type ActivityLogEntry } from "../lib/activity-log";

const logs: ActivityLogEntry[] = [
  {
    id: 1,
    userId: "u1",
    userName: "יוסי כהן",
    userEmail: "yossi@org.com",
    userRole: "admin",
    actionType: "login",
    entityType: "session",
    entityId: null,
    objectLabel: "כניסה למערכת",
    description: "התחבר למערכת",
    createdAt: "2026-03-16T08:30:00.000Z",
  },
  {
    id: 2,
    userId: "u2",
    userName: "שרה לוי",
    userEmail: "sara@org.com",
    userRole: "project_manager",
    actionType: "update",
    entityType: "project",
    entityId: "12",
    objectLabel: "פרויקט גבעתיים",
    description: "עדכן את פרטי הפרויקט",
    createdAt: "2026-03-17T09:45:00.000Z",
  },
];

describe("activity log helpers", () => {
  it("filters logs by free text and action type", () => {
    const result = filterActivityLogs(logs, {
      searchTerm: "גבעתיים",
      userId: "",
      actionType: "update",
      entityType: "",
      startDate: "",
      endDate: "",
    });

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(2);
  });

  it("formats dates in DD/MM/YYYY HH:mm", () => {
    expect(formatActivityDateTime("2026-03-16T08:30:00.000Z")).toMatch(/^\d{2}\/\d{2}\/\d{4} \d{2}:\d{2}$/);
  });

  it("keeps only exact-stage projects for auto distribution", () => {
    const lessonStage = "תכנון";
    const aiProjects = [
      { project_name: "פרויקט א", stage: "תכנון" },
      { project_name: "פרויקט ב", stage: "בדיקות" },
    ];

    const exactStageProjects = aiProjects.filter((project) => project.stage === lessonStage);

    expect(exactStageProjects).toHaveLength(1);
    expect(exactStageProjects[0].project_name).toBe("פרויקט א");
  });

  it("keeps only AI-recommended referents with matching stage", () => {
    const lessonStageIndex = 2;
    const recommendedReferents = ["u10", "u11"];
    const referents = [
      { id: "u10", assignedStageIndexes: [2, 5] },
      { id: "u11", assignedStageIndexes: [7] },
    ];

    const validReferentIds = referents
      .filter((referent) => recommendedReferents.includes(referent.id) && referent.assignedStageIndexes.includes(lessonStageIndex))
      .map((referent) => referent.id);

    expect(validReferentIds).toEqual(["u10"]);
  });
});