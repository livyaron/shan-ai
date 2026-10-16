import { describe, expect, it } from "vitest";
import {
  approvalReferent,
  canSubmitDraft,
  initialLessonState,
  isVisibleLesson,
  missingReviewReferents,
  referentOptions,
} from "@/lib/lessonAssignment";

const users = [
  { id: "r1", name: "רפרנט חשמל", role: "referent" },
  { id: "r2", name: "רפרנט אזרחי", role: "referent" },
  { id: "pm", name: "מנהל פרויקט", role: "project_manager" },
  { id: "ad", name: "מנהל מערכת", role: "admin" },
];

describe("referent picker", () => {
  it("lists referents only", () => {
    expect(referentOptions(users).map((o) => o.value)).toEqual(["r1", "r2"]);
  });

  it("keeps a legacy non-referent assignment visible instead of dropping it", () => {
    const opts = referentOptions(users, "pm");
    expect(opts.map((o) => o.value)).toEqual(["pm", "r1", "r2"]);
    expect(opts[0].label).toContain("לא רפרנט");
  });
});

describe("draft vs. sent for approval", () => {
  it("a lesson with a referent goes to approval", () => {
    expect(initialLessonState(users, "r1")).toEqual({ status: "new", workflowStatus: "חדש" });
  });

  it("no referent, or a non-referent, makes a draft", () => {
    expect(initialLessonState(users, undefined).status).toBe("draft");
    expect(initialLessonState(users, "pm").status).toBe("draft");
  });

  it("a draft is visible to its creator only", () => {
    const draft = { id: 1, status: "draft", createdBy: "pm" };
    expect(isVisibleLesson(draft, "pm")).toBe(true);
    expect(isVisibleLesson(draft, "ad")).toBe(false);
    expect(isVisibleLesson(draft, "r1")).toBe(false);
    expect(isVisibleLesson({ ...draft, status: "new" }, "ad")).toBe(true);
  });

  it("only the creator submits, and only with a referent", () => {
    const draft = { id: 1, status: "draft", createdBy: "pm", assignedTo: "r1",
      title: "t", project: "p", stage: "תכנון", category: "c", risk: "low" };
    expect(canSubmitDraft(draft, "pm", users)).toBe(true);
    // A phone draft saved with the minimum cannot skip the classification.
    expect(canSubmitDraft({ ...draft, stage: "" }, "pm", users)).toBe(false);
    expect(canSubmitDraft({ ...draft, category: " " }, "pm", users)).toBe(false);
    expect(canSubmitDraft(draft, "ad", users)).toBe(false);
    expect(canSubmitDraft({ ...draft, assignedTo: undefined }, "pm", users)).toBe(false);
    expect(canSubmitDraft({ ...draft, status: "new" }, "pm", users)).toBe(false);
  });
});

describe("approval hand-off", () => {
  it("hands the lesson to the referent its creator picked", () => {
    expect(approvalReferent({ id: 1, status: "new", createdBy: "pm", assignedTo: "r2" }, users)).toBe("r2");
  });

  it("falls back to the manual flow for a legacy non-referent assignment", () => {
    expect(approvalReferent({ id: 1, status: "new", createdBy: "pm", assignedTo: "pm" }, users)).toBeNull();
    expect(approvalReferent({ id: 1, status: "new", createdBy: "pm" }, users)).toBeNull();
  });

  it("never creates a second review row for the same referent", () => {
    const reviews = [{ lessonId: 1, referentId: "r1" }, { lessonId: 2, referentId: "r2" }];
    expect(missingReviewReferents(reviews, 1, ["r1", "r2", "r2"])).toEqual(["r2"]);
    expect(missingReviewReferents(reviews, 1, ["r1"])).toEqual([]);
  });
});
