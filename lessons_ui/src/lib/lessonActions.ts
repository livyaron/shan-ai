/**
 * What a user has to do on a lesson, and where each action leads — the one rule
 * for the computer's lesson page and the phone (PLAN-lessons-mobile.md M3).
 * Pure: no React, no network.
 */

export const WF_AWAITING_ADMIN = "ממתין לאישור מנהל מערכת";
export const WF_IN_REFERENT = "בטיפול רפרנט";
export const WF_AWAITING_CREATOR = "ממתין להשלמת יוצר";
export const WF_APPROVED = "אושר והופץ";
export const WF_REJECTED = "נדחה";

/** Referent decisions that close the lesson as rejected; every other one goes to the admin. */
const REJECTING_DECISIONS = new Set(["לא רלוונטי", "הלקח נדחה"]);

export const referentDecisionNext = (decision: string): string =>
  REJECTING_DECISIONS.has(decision) ? WF_REJECTED : WF_AWAITING_ADMIN;

/** "סיימתי להשלים" returns the lesson to whoever asked for the completion. */
export const creatorCompletionTarget = (lesson: { returnedBy?: string }): string =>
  lesson.returnedBy === "referent" ? WF_IN_REFERENT : WF_AWAITING_ADMIN;

/** The admin's final step on a lesson the referent handled. */
export const ADMIN_FINAL_ACTIONS = {
  approve: WF_APPROVED,
  reject: WF_REJECTED,
  toReferent: WF_IN_REFERENT,
} as const;
export type AdminFinalAction = keyof typeof ADMIN_FINAL_ACTIONS;

// ---------------------------------------------------------------- AI distribution

interface ProjectLike {
  id: number;
  name: string;
  stageIndex: number;
}

/**
 * Which AI-recommended projects are pre-selected for distribution: a known
 * project in the lesson's own stage. The rest are listed as filtered out.
 */
export const aiDistributionTargets = (
  recommended: { project_name: string }[],
  projects: ProjectLike[],
  lessonStageIndex: number,
): { selected: number[]; filteredOut: string[] } => {
  const selected: number[] = [];
  const filteredOut: string[] = [];
  for (const r of recommended) {
    const p = projects.find((x) => x.name === r.project_name);
    if (p && p.stageIndex === lessonStageIndex) selected.push(p.id);
    else filteredOut.push(r.project_name);
  }
  return { selected, filteredOut };
};

// ---------------------------------------------------------------- my tasks

interface UserLike {
  id: string;
  role: string;
  assignedProjects: number[];
}

interface LessonLike {
  id: number;
  status: string;
  workflowStatus: string;
  createdBy: string;
  assignedTo?: string;
  distributedToReferents?: string[];
}

interface ReviewLike {
  id: number;
  lessonId: number;
  referentId: string;
  respondedAt?: string;
}

interface ImplLike {
  id: number;
  lessonId: number;
  projectId: number;
  respondedBy?: string;
}

export type TaskKind =
  | "approve_new"
  | "admin_final"
  | "referent_decide"
  | "referent_feedback"
  | "pm_feedback"
  | "creator_complete";

export const TASK_LABELS: Record<TaskKind, string> = {
  approve_new: "ממתין לאישורך",
  admin_final: "אישור סופי",
  referent_decide: "ממתין להחלטתך",
  referent_feedback: "ממתין לתגובתך",
  pm_feedback: "תגובה לפרויקט שלך",
  creator_complete: "הוחזר אליך להשלמה",
};

export interface LessonTask {
  kind: TaskKind;
  lessonId: number;
  /** referent_reviews row for referent tasks, lesson_implementations row for pm_feedback */
  rowId?: number;
  projectId?: number;
}

/** A lesson mature enough for project / referent feedback (same as the computer's "needs action"). */
const actionable = (l: LessonLike) => l.status === "approved" || l.status === "distributed";

/** The referent's decision card (computer: "טיפול רפרנט"). */
export const canReferentDecide = (lesson: LessonLike, user: UserLike, reviews: ReviewLike[]): boolean =>
  (user.role === "referent" || user.id === lesson.assignedTo) &&
  lesson.workflowStatus === WF_IN_REFERENT &&
  reviews.some((r) => r.lessonId === lesson.id && r.referentId === user.id);

export const canAdminFinalize = (lesson: LessonLike, user: UserLike): boolean =>
  user.role === "admin" && lesson.workflowStatus === WF_AWAITING_ADMIN;

export const canCreatorComplete = (lesson: LessonLike, user: UserLike): boolean =>
  lesson.workflowStatus === WF_AWAITING_CREATOR && lesson.createdBy === user.id;

/** Everything waiting for this user, one entry per thing to do. */
export const pendingTasks = (
  user: UserLike,
  lessons: LessonLike[],
  reviews: ReviewLike[],
  impls: ImplLike[],
): LessonTask[] => {
  const tasks: LessonTask[] = [];
  const byId = new Map(lessons.map((l) => [l.id, l]));
  for (const l of lessons) {
    if (user.role === "admin" && l.status === "new") tasks.push({ kind: "approve_new", lessonId: l.id });
    if (canAdminFinalize(l, user)) tasks.push({ kind: "admin_final", lessonId: l.id });
    if (canCreatorComplete(l, user)) tasks.push({ kind: "creator_complete", lessonId: l.id });
    if (canReferentDecide(l, user, reviews)) {
      const r = reviews.find((x) => x.lessonId === l.id && x.referentId === user.id);
      tasks.push({ kind: "referent_decide", lessonId: l.id, rowId: r?.id });
    }
  }
  for (const r of reviews) {
    const l = byId.get(r.lessonId);
    // A lesson in the referent's decision step is already a referent_decide task.
    if (!l || r.referentId !== user.id || r.respondedAt || !actionable(l) || canReferentDecide(l, user, reviews)) continue;
    if (l.distributedToReferents?.includes(user.id)) tasks.push({ kind: "referent_feedback", lessonId: l.id, rowId: r.id });
  }
  // A project's response is its PROJECT MANAGER's task — the same rule as the computer's
  // useActionRequired. An admin linked to every project is not waiting on 200 of them.
  for (const i of impls) {
    if (user.role !== "project_manager") break;
    const l = byId.get(i.lessonId);
    if (!l || i.respondedBy || !actionable(l) || !user.assignedProjects.includes(i.projectId)) continue;
    tasks.push({ kind: "pm_feedback", lessonId: l.id, rowId: i.id, projectId: i.projectId });
  }
  return tasks;
};
