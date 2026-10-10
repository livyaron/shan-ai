import { describe, it, expect } from "vitest";
import type { Lesson } from "@/context/UserContext";
import { classifyLesson } from "@/lib/lessonStatus";
import {
  lessonsCreatedInRange,
  bucketBreakdown,
  isDateInRange,
  parseLessonDate,
} from "@/lib/newsletterQuery";

/** Minimal lesson factory — only the fields the classification/query touch. */
const makeLesson = (overrides: Partial<Lesson> = {}): Lesson => ({
  id: overrides.id ?? 1,
  title: "לקח",
  description: "",
  recommendation: "",
  project: "",
  projectId: null,
  stage: "",
  category: "",
  risk: "low",
  status: "new",
  date: "2026-05-10",
  createdBy: "u1",
  equipmentIds: [],
  workflowStatus: "",
  ...overrides,
});

describe("classifyLesson — single source of truth for status buckets", () => {
  it("classifies approved statuses as 'approved'", () => {
    expect(classifyLesson(makeLesson({ status: "approved" }))).toBe("approved");
    expect(classifyLesson(makeLesson({ status: "distributed" }))).toBe("approved");
    expect(classifyLesson(makeLesson({ status: "closed" }))).toBe("approved");
  });

  it("classifies the approved workflow label as 'approved'", () => {
    expect(classifyLesson(makeLesson({ status: "new", workflowStatus: "אושר והופץ" }))).toBe("approved");
  });

  it("classifies the creator-completion workflow as 'draft'", () => {
    expect(classifyLesson(makeLesson({ status: "new", workflowStatus: "ממתין להשלמת יוצר" }))).toBe("draft");
  });

  it("classifies everything else as 'pending'", () => {
    expect(classifyLesson(makeLesson({ status: "new", workflowStatus: "ממתין לאישור מנהל" }))).toBe("pending");
  });
});

describe("newsletterQuery — canonical date-range query", () => {
  const start = new Date(2026, 4, 1, 0, 0, 0, 0); // 01/05/2026
  const end = new Date(2026, 4, 31, 23, 59, 59, 999); // 31/05/2026

  it("parses valid dates and rejects invalid/missing ones", () => {
    expect(parseLessonDate("2026-05-10")).toBeInstanceOf(Date);
    expect(parseLessonDate("")).toBeNull();
    expect(parseLessonDate(undefined)).toBeNull();
    expect(parseLessonDate("not-a-date")).toBeNull();
  });

  it("includes only lessons whose creation date is inside the range", () => {
    const lessons = [
      makeLesson({ id: 1, date: "2026-05-10" }), // in
      makeLesson({ id: 2, date: "2026-04-30" }), // before
      makeLesson({ id: 3, date: "2026-06-01" }), // after
      makeLesson({ id: 4, date: "" }), // invalid
      makeLesson({ id: 5, date: "2026-05-31" }), // in (boundary)
    ];
    const result = lessonsCreatedInRange(lessons, { start, end });
    expect(result.map((l) => l.id).sort()).toEqual([1, 5]);
  });

  it("bucketBreakdown totals always equal the sum of buckets", () => {
    const lessons = [
      makeLesson({ id: 1, status: "approved" }),
      makeLesson({ id: 2, status: "new", workflowStatus: "ממתין לאישור מנהל" }),
      makeLesson({ id: 3, status: "new", workflowStatus: "ממתין להשלמת יוצר" }),
      makeLesson({ id: 4, status: "distributed" }),
    ];
    const b = bucketBreakdown(lessons);
    expect(b.total).toBe(4);
    expect(b.approved).toBe(2);
    expect(b.pending).toBe(1);
    expect(b.draft).toBe(1);
    expect(b.approved + b.pending + b.draft).toBe(b.total);
  });

  it("isDateInRange is inclusive of both boundaries", () => {
    expect(isDateInRange(start, start, end)).toBe(true);
    expect(isDateInRange(end, start, end)).toBe(true);
    expect(isDateInRange(new Date(2026, 3, 30), start, end)).toBe(false);
  });
});
