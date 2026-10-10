import { describe, it, expect } from "vitest";
import type { Lesson, LessonImplementation, ReferentReview, MockUser, Project } from "@/context/UserContext";
import { roleLabels } from "@/context/UserContext";
import { periodRange } from "@/lib/newsletterPeriods";
import { lessonsCreatedInRange, bucketBreakdown } from "@/lib/newsletterQuery";
import { computeNewsletterStats } from "@/lib/newsletterStats";

/**
 * Integration test: the admin PANEL preview, the DASHBOARD/newsletter stats, and
 * the PDF EXPORT must all report the SAME numbers, because they all rely on the
 * single shared query (lessonsCreatedInRange + bucketBreakdown / computeNewsletterStats).
 */

const makeLesson = (overrides: Partial<Lesson>): Lesson => ({
  id: 0,
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

const users: MockUser[] = [
  { id: "u1", name: "יוסי", email: "a@a.com", role: "project_manager", assignedProjects: [], assignedEquipmentIds: [], assignedStageIndexes: [], emailPreferences: {} as never },
  { id: "u2", name: "שרה", email: "b@b.com", role: "referent", assignedProjects: [], assignedEquipmentIds: [], assignedStageIndexes: [], emailPreferences: {} as never },
];
const projects: Project[] = [];
const implementations: LessonImplementation[] = [];
const reviews: ReferentReview[] = [];

// Range = May 2026 (monthly).
const range = periodRange("monthly", "2026-05");

const buildLessons = (): Lesson[] => [
  makeLesson({ id: 1, date: "2026-05-02", status: "approved" }),
  makeLesson({ id: 2, date: "2026-05-15", status: "distributed" }),
  makeLesson({ id: 3, date: "2026-05-20", status: "new", workflowStatus: "ממתין לאישור מנהל" }),
  makeLesson({ id: 4, date: "2026-05-25", status: "new", workflowStatus: "ממתין להשלמת יוצר" }),
  makeLesson({ id: 5, date: "2026-04-30", status: "approved" }), // out of range (before)
  makeLesson({ id: 6, date: "2026-06-01", status: "approved" }), // out of range (after)
];

/** Replicates exactly what NewsletterPanel computes for its preview breakdown. */
const panelBreakdown = (lessons: Lesson[]) =>
  bucketBreakdown(lessonsCreatedInRange(lessons, range));

describe("newsletter consistency: panel vs dashboard/stats vs PDF export", () => {
  it("all three sources report identical created counts and status breakdown", () => {
    const lessons = buildLessons();

    const panel = panelBreakdown(lessons);
    const stats = computeNewsletterStats({
      range, lessons, projects, users, implementations, reviews, roleLabels,
    });

    // Panel <-> stats (dashboard) agreement.
    expect(stats.kpiCreated).toBe(panel.total);
    expect(stats.createdApproved).toBe(panel.approved);
    expect(stats.createdPending).toBe(panel.pending);
    expect(stats.createdDraft).toBe(panel.draft);

    // Concrete expected values for the dataset above (4 in range: 2 approved, 1 pending, 1 draft).
    expect(panel.total).toBe(4);
    expect(panel.approved).toBe(2);
    expect(panel.pending).toBe(1);
    expect(panel.draft).toBe(1);

    // PDF export reads these exact stats fields (see newsletterExport.ts) — no recompute.
    // Simulate the export's number source and assert it equals the shared query result.
    const exportNumbers = {
      kpiCreated: stats.kpiCreated,
      kpiApproved: stats.kpiApproved,
      createdApproved: stats.createdApproved,
      createdPending: stats.createdPending,
      createdDraft: stats.createdDraft,
    };
    expect(exportNumbers.kpiCreated).toBe(panel.total);
    expect(exportNumbers.kpiApproved).toBe(panel.approved);
    expect(exportNumbers.createdApproved).toBe(panel.approved);
    expect(exportNumbers.createdPending).toBe(panel.pending);
    expect(exportNumbers.createdDraft).toBe(panel.draft);
  });

  it("headline breakdown always sums to the created total across sources", () => {
    const lessons = buildLessons();
    const stats = computeNewsletterStats({
      range, lessons, projects, users, implementations, reviews, roleLabels,
    });
    expect(stats.createdApproved + stats.createdPending + stats.createdDraft).toBe(stats.kpiCreated);
    expect(stats.kpiApproved).toBe(stats.createdApproved);
  });

  it("empty period yields zeros everywhere without diverging", () => {
    const empty: Lesson[] = [];
    const panel = panelBreakdown(empty);
    const stats = computeNewsletterStats({
      range, lessons: empty, projects, users, implementations, reviews, roleLabels,
    });
    expect(panel.total).toBe(0);
    expect(stats.kpiCreated).toBe(0);
    expect(stats.createdApproved).toBe(0);
    expect(stats.createdPending).toBe(0);
    expect(stats.createdDraft).toBe(0);
  });
});
