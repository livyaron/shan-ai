import { describe, expect, it } from "vitest";
import {
  WF_AWAITING_ADMIN,
  WF_AWAITING_CREATOR,
  WF_IN_REFERENT,
  WF_REJECTED,
  aiDistributionTargets,
  canAdminFinalize,
  canCreatorComplete,
  canReferentDecide,
  creatorCompletionTarget,
  pendingTasks,
  referentDecisionNext,
} from "@/lib/lessonActions";

const admin = { id: "ad", role: "admin", assignedProjects: [] };
const ref = { id: "r1", role: "referent", assignedProjects: [] };
const pm = { id: "pm", role: "project_manager", assignedProjects: [7] };
const L = (o: Partial<{ id: number; status: string; workflowStatus: string; createdBy: string; assignedTo: string; distributedToReferents: string[]; returnedBy: string }>) =>
  ({ id: 1, status: "approved", workflowStatus: "חדש", createdBy: "pm", ...o });

describe("where each step leads (one rule with the computer)", () => {
  it("a rejecting referent decision closes the lesson, any other goes to the admin", () => {
    expect(referentDecisionNext("לא רלוונטי")).toBe(WF_REJECTED);
    expect(referentDecisionNext("הלקח נדחה")).toBe(WF_REJECTED);
    expect(referentDecisionNext("הלקח יושם")).toBe(WF_AWAITING_ADMIN);
    expect(referentDecisionNext("נדרש מידע נוסף")).toBe(WF_AWAITING_ADMIN);
  });

  it("a completed return goes back to whoever asked for it", () => {
    expect(creatorCompletionTarget({ returnedBy: "referent" })).toBe(WF_IN_REFERENT);
    expect(creatorCompletionTarget({ returnedBy: "admin" })).toBe(WF_AWAITING_ADMIN);
    expect(creatorCompletionTarget({})).toBe(WF_AWAITING_ADMIN);
  });

  it("the AI pre-selects known projects in the lesson's own stage only", () => {
    const projects = [{ id: 1, name: "א", stageIndex: 2 }, { id: 2, name: "ב", stageIndex: 5 }];
    expect(aiDistributionTargets([{ project_name: "א" }, { project_name: "ב" }, { project_name: "לא קיים" }], projects, 2))
      .toEqual({ selected: [1], filteredOut: ["ב", "לא קיים"] });
  });
});

describe("who may act", () => {
  const reviews = [{ id: 9, lessonId: 1, referentId: "r1" }];
  it("the referent decides only in the referent step and with a review row", () => {
    expect(canReferentDecide(L({ workflowStatus: WF_IN_REFERENT }), ref, reviews)).toBe(true);
    expect(canReferentDecide(L({ workflowStatus: WF_AWAITING_ADMIN }), ref, reviews)).toBe(false);
    expect(canReferentDecide(L({ workflowStatus: WF_IN_REFERENT }), ref, [])).toBe(false);
    expect(canReferentDecide(L({ workflowStatus: WF_IN_REFERENT }), pm, reviews)).toBe(false);
  });
  it("final approval is the admin's; completion is the creator's", () => {
    expect(canAdminFinalize(L({ workflowStatus: WF_AWAITING_ADMIN }), admin)).toBe(true);
    expect(canAdminFinalize(L({ workflowStatus: WF_AWAITING_ADMIN }), pm)).toBe(false);
    expect(canCreatorComplete(L({ workflowStatus: WF_AWAITING_CREATOR }), pm)).toBe(true);
    expect(canCreatorComplete(L({ workflowStatus: WF_AWAITING_CREATOR, createdBy: "x" }), pm)).toBe(false);
  });
});

describe("my tasks", () => {
  const lessons = [
    L({ id: 1, status: "new", workflowStatus: "חדש" }),
    L({ id: 2, workflowStatus: WF_AWAITING_ADMIN }),
    L({ id: 3, workflowStatus: WF_IN_REFERENT, assignedTo: "r1" }),
    L({ id: 4, status: "distributed", workflowStatus: "בטיפול", distributedToReferents: ["r1"] }),
    L({ id: 5, workflowStatus: WF_AWAITING_CREATOR }),
    L({ id: 6, status: "draft", workflowStatus: "טיוטה" }),
  ];
  const reviews = [
    { id: 30, lessonId: 3, referentId: "r1" },
    { id: 40, lessonId: 4, referentId: "r1" },
    { id: 41, lessonId: 4, referentId: "r2" },
  ];
  const impls = [
    { id: 70, lessonId: 4, projectId: 7 },
    { id: 71, lessonId: 4, projectId: 8 },
    { id: 72, lessonId: 1, projectId: 7 },
    { id: 73, lessonId: 2, projectId: 7, respondedBy: "pm" },
  ];
  const kinds = (u: typeof admin) => pendingTasks(u, lessons, reviews, impls).map((t) => `${t.kind}:${t.lessonId}:${t.rowId ?? ""}`);

  it("admin: approve the new one, finalize the handled one", () => {
    expect(kinds(admin)).toEqual(["approve_new:1:", "admin_final:2:"]);
  });

  it("referent: decide in the referent step, feedback on a distributed lesson — never both for one row", () => {
    expect(kinds(ref)).toEqual(["referent_decide:3:30", "referent_feedback:4:40"]);
  });

  it("PM: their own project's open feedback on a mature lesson, and the lesson returned to them", () => {
    // impl 72 belongs to a lesson still "new"; 73 is answered; 71 is not their project.
    expect(kinds(pm)).toEqual(["creator_complete:5:", "pm_feedback:4:70"]);
  });

  it("project responses are a project manager's task only — never an admin's (bug 2026-10-17: 209)", () => {
    const linkedAdmin = { ...admin, assignedProjects: [7, 8] };
    expect(kinds(linkedAdmin)).toEqual(["approve_new:1:", "admin_final:2:"]);
    const linkedRef = { ...ref, assignedProjects: [7] };
    expect(kinds(linkedRef)).not.toContain("pm_feedback:4:70");
  });

  it("answered rows are not tasks", () => {
    const done = pendingTasks(ref, lessons, reviews.map((r) => ({ ...r, respondedAt: "t" })), []);
    expect(done.map((t) => t.kind)).toEqual(["referent_decide"]);
  });
});
