import { afterEach, describe, expect, it } from "vitest";
import {
  PAST_PROJECT,
  clearLocalDraft,
  emptyForm,
  formMissingForSave,
  formMissingForSubmit,
  fromLesson,
  hasContent,
  loadLocalDraft,
  localDraftKey,
  saveLocalDraft,
  selectableProjects,
  stepGaps,
  toLessonInput,
  todayIso,
} from "@/lib/lessonDraft";
import { canSubmitDraft, missingForSave } from "@/lib/lessonAssignment";
import { fitWithin } from "@/lib/lessonFiles";

const users = [
  { id: "r1", name: "רפרנט חשמל", role: "referent" },
  { id: "pm", name: "מנהל פרויקט", role: "project_manager", assignedProjects: [3] },
  { id: "ad", name: "מנהל מערכת", role: "admin" },
];
const projects = [
  { id: 1, name: "תחנה א", managerId: "pm" },
  { id: 2, name: "תחנה ב", managerId: "other" },
  { id: 3, name: "תחנה ג", managerId: "pm" },
];

const minimal = () => ({ ...emptyForm("2026-10-16"), title: "כבל נחתך", description: "קרה בחפירה", projectId: "1", risk: "high" as const });

describe("what the wizard needs (one rule with the computer)", () => {
  it("saves a draft with title, what happened, project and risk only", () => {
    expect(formMissingForSave(emptyForm(), projects)).toEqual(["title", "description", "project", "risk"]);
    expect(formMissingForSave(minimal(), projects)).toEqual([]);
  });

  it("sends for approval only with stage, category and a referent too", () => {
    expect(formMissingForSubmit(minimal(), projects, users)).toEqual(["stage", "category", "assignedTo"]);
    const full = { ...minimal(), stage: "תכנון", category: "בטיחות", assignedTo: "r1" };
    expect(formMissingForSubmit(full, projects, users)).toEqual([]);
    // A non-referent in the field is not a referent.
    expect(formMissingForSubmit({ ...full, assignedTo: "pm" }, projects, users)).toEqual(["assignedTo"]);
  });

  it("whitespace is not content", () => {
    expect(formMissingForSave({ ...minimal(), title: "   " }, projects)).toEqual(["title"]);
  });

  it("a historical project counts only with a name", () => {
    const past = { ...minimal(), projectId: PAST_PROJECT, pastProjectName: "" };
    expect(formMissingForSave(past, projects)).toEqual(["project"]);
    expect(formMissingForSave({ ...past, pastProjectName: "תחנה ישנה" }, projects)).toEqual([]);
  });

  it("an unknown project id is no project", () => {
    expect(formMissingForSave({ ...minimal(), projectId: "999" }, projects)).toEqual(["project"]);
  });

  it("the computer's submit button follows the same rule", () => {
    const draft = { id: 1, status: "draft", createdBy: "pm", title: "t", description: "", project: "p", stage: "", category: "c", risk: "low", assignedTo: "r1" };
    expect(canSubmitDraft(draft, "pm", users)).toBe(false);
    expect(canSubmitDraft({ ...draft, stage: "תכנון" }, "pm", users)).toBe(true);
    expect(missingForSave(draft)).toEqual(["description"]);
  });

  it("marks each step with its own gaps", () => {
    const gaps = stepGaps(minimal(), projects, users);
    expect(gaps).toEqual([[], [], ["stage", "category"], ["assignedTo"]]);
  });
});

describe("projects a user may pick", () => {
  it("a PM gets the projects they manage, an admin all", () => {
    expect(selectableProjects(projects, users[1]).map((p) => p.id).sort()).toEqual([1, 3]);
    expect(selectableProjects(projects, users[2])).toHaveLength(3);
    expect(selectableProjects(projects, users[0])).toHaveLength(3);
  });

  it("puts the user's own projects first", () => {
    const admin = { id: "ad", name: "a", role: "admin", assignedProjects: [2] };
    expect(selectableProjects(projects, admin)[0].id).toBe(2);
  });
});

describe("form ⇄ lesson", () => {
  it("builds the insert payload", () => {
    const input = toLessonInput({ ...minimal(), impactScheduleDelay: "12", impactBudgetCost: "", stage: "תכנון" }, projects);
    expect(input).toMatchObject({ title: "כבל נחתך", project: "תחנה א", projectId: 1, risk: "high", stage: "תכנון", impactScheduleDelay: 12, eventDate: "2026-10-16" });
    expect(input.impactBudgetCost).toBeUndefined();
    expect(input.assignedTo).toBeUndefined();
  });

  it("a historical project has a name and no id", () => {
    const input = toLessonInput({ ...minimal(), projectId: PAST_PROJECT, pastProjectName: " ישנה " }, projects);
    expect(input).toMatchObject({ project: "ישנה", projectId: null });
  });

  it("round-trips an existing draft", () => {
    const lesson = { ...toLessonInput({ ...minimal(), assignedTo: "r1", impactBudgetCost: "500" }, projects) };
    const back = fromLesson(lesson);
    expect(back).toMatchObject({ title: "כבל נחתך", projectId: "1", risk: "high", assignedTo: "r1", impactBudgetCost: "500" });
    const legacy = fromLesson({ ...lesson, projectId: null, project: "תחנה ישנה" });
    expect(legacy).toMatchObject({ projectId: PAST_PROJECT, pastProjectName: "תחנה ישנה" });
  });

  it("today is the local calendar day", () => {
    expect(todayIso(new Date(2026, 0, 5, 1, 30))).toBe("2026-01-05");
  });
});

describe("the copy kept on the phone (M-D4)", () => {
  afterEach(() => localStorage.clear());

  it("is per user and per lesson, and survives a reload", () => {
    saveLocalDraft("pm", { form: minimal(), lessonId: null, savedAt: "t" });
    saveLocalDraft("pm", { form: { ...minimal(), title: "אחר" }, lessonId: 7, savedAt: "t" });
    expect(loadLocalDraft("pm", null)?.form.title).toBe("כבל נחתך");
    expect(loadLocalDraft("pm", 7)?.form.title).toBe("אחר");
    expect(loadLocalDraft("ad", null)).toBeNull();
    expect(localDraftKey("pm", null)).toBe("lessons.wizard.pm.new");
    clearLocalDraft("pm", null);
    expect(loadLocalDraft("pm", null)).toBeNull();
  });

  it("a broken or old copy never crashes the wizard", () => {
    localStorage.setItem(localDraftKey("pm", null), "{not json");
    expect(loadLocalDraft("pm", null)).toBeNull();
    localStorage.setItem(localDraftKey("pm", 1), JSON.stringify({ form: { title: "ישן" }, lessonId: 1, savedAt: "t" }));
    expect(loadLocalDraft("pm", 1)?.form).toMatchObject({ title: "ישן", equipmentIds: [], risk: "" });
  });

  it("an untouched form is not worth keeping", () => {
    expect(hasContent(emptyForm())).toBe(false);
    expect(hasContent({ ...emptyForm(), risk: "low" })).toBe(true);
  });
});

describe("photo size", () => {
  it("shrinks the long side to 1600 and keeps the ratio", () => {
    expect(fitWithin(4000, 3000)).toEqual({ w: 1600, h: 1200 });
    expect(fitWithin(3000, 4000)).toEqual({ w: 1200, h: 1600 });
    expect(fitWithin(800, 600)).toEqual({ w: 800, h: 600 });
  });
});
