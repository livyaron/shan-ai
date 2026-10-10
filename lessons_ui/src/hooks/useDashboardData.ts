import { useMemo } from "react";
import type { Lesson, Project, ReferentReview, LessonImplementation, MockUser } from "@/context/UserContext";

/**
 * Centralized role-based dashboard data filtering.
 *
 * Source-of-truth rules (must stay consistent with Lessons.tsx & Projects.tsx):
 * - admin: full org-wide data
 * - project_manager: projects where project.managerId === currentUser.id;
 *                    lessons created by user OR linked to one of those projects
 * - referent: lessons distributed to the referent OR ones the referent reviewed;
 *             projects derived from those lessons only
 */
export interface DashboardData {
  lessons: Lesson[];
  projects: Project[];
  referentReviews: ReferentReview[];
  implementations: LessonImplementation[];
  myProjectIds: number[];
}

export const useDashboardData = (
  currentUser: MockUser,
  allLessons: Lesson[],
  allProjects: Project[],
  allReferentReviews: ReferentReview[],
  allImplementations: LessonImplementation[],
): DashboardData => {
  return useMemo(() => {
    if (currentUser.role === "admin" || currentUser.role === "viewer") {
      return {
        lessons: allLessons,
        projects: allProjects,
        referentReviews: allReferentReviews,
        implementations: allImplementations,
        myProjectIds: allProjects.map((p) => p.id),
      };
    }

    if (currentUser.role === "referent") {
      const myReviews = allReferentReviews.filter((r) => r.referentId === currentUser.id);
      const reviewedLessonIds = new Set(myReviews.map((r) => r.lessonId));
      const lessons = allLessons.filter(
        (l) => l.distributedToReferents?.includes(currentUser.id) || reviewedLessonIds.has(l.id),
      );
      const projectIds = new Set<number>();
      lessons.forEach((l) => {
        if (l.projectId) projectIds.add(l.projectId);
      });
      const projects = allProjects.filter((p) => projectIds.has(p.id));
      const lessonIdSet = new Set(lessons.map((l) => l.id));
      return {
        lessons,
        projects,
        referentReviews: myReviews,
        implementations: allImplementations.filter((i) => lessonIdSet.has(i.lessonId)),
        myProjectIds: [...projectIds],
      };
    }

    // project_manager — single source of truth: project.managerId === currentUser.id
    const myProjectIds = allProjects
      .filter((p) => p.managerId === currentUser.id)
      .map((p) => p.id);
    const myProjectIdSet = new Set(myProjectIds);
    const lessons = allLessons.filter(
      (l) =>
        l.createdBy === currentUser.id ||
        (l.projectId !== null && l.projectId !== undefined && myProjectIdSet.has(l.projectId)),
    );
    const projects = allProjects.filter((p) => myProjectIdSet.has(p.id));
    const lessonIdSet = new Set(lessons.map((l) => l.id));
    return {
      lessons,
      projects,
      referentReviews: allReferentReviews.filter((r) => lessonIdSet.has(r.lessonId)),
      implementations: allImplementations.filter((i) => lessonIdSet.has(i.lessonId)),
      myProjectIds,
    };
  }, [currentUser, allLessons, allProjects, allReferentReviews, allImplementations]);
};
