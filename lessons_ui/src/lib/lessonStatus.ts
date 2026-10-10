/**
 * Single source of truth for lesson status classification.
 * Used by the newsletter (and available to dashboard/system) so counts stay consistent.
 *
 * Buckets:
 *  - "approved" — the lesson was approved and distributed (Status = Approved).
 *  - "pending"  — waiting for manager approval / referent handling (still in flow).
 *  - "draft"    — returned to creator for completion (not yet submitted for approval).
 */
import type { Lesson } from "@/context/UserContext";

export type LessonBucket = "approved" | "pending" | "draft";

const APPROVED_STATUSES = new Set(["approved", "distributed", "closed"]);
const APPROVED_WORKFLOW = "אושר והופץ";
const DRAFT_WORKFLOW = "ממתין להשלמת יוצר";

/** Classify a lesson into a single, canonical status bucket. */
export const classifyLesson = (lesson: Lesson): LessonBucket => {
  if (APPROVED_STATUSES.has(lesson.status) || lesson.workflowStatus === APPROVED_WORKFLOW) {
    return "approved";
  }
  if (lesson.workflowStatus === DRAFT_WORKFLOW) {
    return "draft";
  }
  return "pending";
};

/** True when a lesson counts as an approved & distributed lesson. */
export const isApprovedLesson = (lesson: Lesson): boolean => classifyLesson(lesson) === "approved";

export const bucketLabels: Record<LessonBucket, string> = {
  approved: "מאושרים",
  pending: "ממתינים",
  draft: "טיוטות",
};
