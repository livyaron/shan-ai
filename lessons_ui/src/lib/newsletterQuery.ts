/**
 * Single source of truth for the newsletter "lessons created in range" query
 * and the status-bucket breakdown.
 *
 * Every consumer — the admin panel preview, the dashboard/newsletter stats, and
 * the PDF export — MUST use these helpers so the counts are guaranteed identical.
 */
import type { Lesson } from "@/context/UserContext";
import type { PeriodRange } from "./newsletterPeriods";
import { classifyLesson, type LessonBucket } from "./lessonStatus";

/** Parse a date string safely; returns null for missing/invalid values. */
export const parseLessonDate = (value?: string | null): Date | null => {
  if (!value) return null;
  const d = new Date(value);
  return isNaN(d.getTime()) ? null : d;
};

/** True when a date falls inside [start, end] (inclusive). */
export const isDateInRange = (d: Date | null, start: Date, end: Date): boolean =>
  d !== null && d >= start && d <= end;

/**
 * The canonical query: all lessons whose creation date falls in the range.
 * This is the ONE query every consumer counts.
 */
export const lessonsCreatedInRange = (
  lessons: Lesson[],
  range: Pick<PeriodRange, "start" | "end">,
): Lesson[] =>
  lessons.filter((l) => isDateInRange(parseLessonDate(l.date), range.start, range.end));

export interface BucketBreakdown {
  total: number;
  approved: number;
  pending: number;
  draft: number;
}

/** Break a set of lessons down into canonical status buckets via classifyLesson. */
export const bucketBreakdown = (lessons: Lesson[]): BucketBreakdown => {
  let approved = 0;
  let pending = 0;
  let draft = 0;
  for (const lesson of lessons) {
    const bucket: LessonBucket = classifyLesson(lesson);
    if (bucket === "approved") approved += 1;
    else if (bucket === "pending") pending += 1;
    else draft += 1;
  }
  return { total: lessons.length, approved, pending, draft };
};
