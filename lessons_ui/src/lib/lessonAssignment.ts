/**
 * Referent assignment rules — the single place that decides:
 *  - who may be picked as "אחראי ליישום" (profiles with role 'referent' only),
 *  - when a new lesson is a draft (no referent picked → it stays with its creator),
 *  - who sees a draft (its creator only),
 *  - which referent_reviews rows are still missing (never insert a duplicate).
 *
 * Only the lesson's creator picks the referent. Admin approval hands the lesson to
 * that referent automatically; no AI recommendation and no second assignment step.
 */

export const DRAFT_STATUS = "draft";
export const DRAFT_WORKFLOW_STATUS = "טיוטה";
export const NEW_WORKFLOW_STATUS = "חדש";
export const REFERENT_WORKFLOW_STATUS = "בטיפול רפרנט";

interface UserLike {
  id: string;
  name: string;
  role: string;
}

interface LessonLike {
  id: number;
  status: string;
  createdBy: string;
  assignedTo?: string;
}

interface ReviewLike {
  lessonId: number;
  referentId: string;
}

export const isReferent = (users: UserLike[], id: string | null | undefined): boolean =>
  !!id && users.some((u) => u.id === id && u.role === "referent");

/**
 * Options for a referent picker. `keepId` is the value already stored on a lesson:
 * a legacy lesson may point at a non-referent, and editing it must not silently
 * drop that assignment, so it stays in the list (marked) until someone changes it.
 */
export const referentOptions = (users: UserLike[], keepId?: string | null) => {
  const options = users
    .filter((u) => u.role === "referent")
    .map((u) => ({ value: String(u.id), label: u.name }));
  if (keepId && !options.some((o) => o.value === keepId)) {
    const kept = users.find((u) => u.id === keepId);
    if (kept) options.unshift({ value: String(kept.id), label: `${kept.name} (לא רפרנט)` });
  }
  return options;
};

/** A lesson created without a referent is a draft that waits with its creator. */
export const initialLessonState = (users: UserLike[], assignedTo?: string | null) =>
  isReferent(users, assignedTo)
    ? { status: "new", workflowStatus: NEW_WORKFLOW_STATUS }
    : { status: DRAFT_STATUS, workflowStatus: DRAFT_WORKFLOW_STATUS };

export const isDraft = (lesson: Pick<LessonLike, "status">): boolean => lesson.status === DRAFT_STATUS;

/** A draft is visible to its creator only; every other lesson to everyone. */
export const isVisibleLesson = (lesson: LessonLike, viewerId: string): boolean =>
  !isDraft(lesson) || lesson.createdBy === viewerId;

/** The creator may send a draft for approval once a referent is assigned. */
export const canSubmitDraft = (lesson: LessonLike, viewerId: string, users: UserLike[]): boolean =>
  isDraft(lesson) && lesson.createdBy === viewerId && isReferent(users, lesson.assignedTo);

/**
 * The referent approval hands the lesson to, or null — then the existing manual
 * distribution flow applies (legacy lessons, or a non-referent in assigned_to).
 */
export const approvalReferent = (lesson: LessonLike, users: UserLike[]): string | null =>
  isReferent(users, lesson.assignedTo) ? lesson.assignedTo! : null;

/** Referent ids that do not have a review row for this lesson yet (deduplicated). */
export const missingReviewReferents = (reviews: ReviewLike[], lessonId: number, ids: string[]): string[] => {
  const existing = new Set(reviews.filter((r) => r.lessonId === lessonId).map((r) => r.referentId));
  return [...new Set(ids)].filter((id) => !existing.has(id));
};
