import { useMemo } from "react";
import type { Lesson, LessonImplementation, MockUser, Project, ReferentReview } from "@/context/UserContext";

interface ActionInfo {
  needed: boolean;
  label: string;
}

interface ProjectActionInfo {
  needed: boolean;
  count: number;
  label: string;
}

export const useActionRequired = (
  lessons: Lesson[],
  implementations: LessonImplementation[],
  projects: Project[],
  currentUser: MockUser,
  referentReviews?: ReferentReview[]
) => {
  // Implementations that require a PM response should only be for lessons that are mature enough
  // to act on (e.g. approved/distributed) — otherwise projects get flagged with “no real action”.
  const actionableLessonIds = useMemo(() => {
    return new Set(
      lessons
        .filter((l) => l.status === "approved" || l.status === "distributed")
        .map((l) => l.id)
    );
  }, [lessons]);

  const lessonNeedsAction = useMemo(() => {
    return (lessonId: number): ActionInfo => {
      const lesson = lessons.find((l) => l.id === lessonId);
      if (!lesson) return { needed: false, label: "" };

      if (currentUser.role === "admin") {
        if (lesson.status === "new") {
          return { needed: true, label: "ממתין לאישור" };
        }
      }

      if (currentUser.role === "project_manager") {
        // Rejected lesson created by this user
        if (lesson.status === "rejected" && lesson.createdBy === currentUser.id) {
          return { needed: true, label: "נדחה — דורש תיקון" };
        }

        // Implementation pending response in user's projects (only for actionable lessons)
        if (actionableLessonIds.has(lessonId)) {
          const myProjectIds = currentUser.assignedProjects;
          const pendingImpls = implementations.filter(
            (impl) =>
              impl.lessonId === lessonId &&
              !impl.respondedBy &&
              myProjectIds.includes(impl.projectId)
          );
          if (pendingImpls.length > 0) {
            return { needed: true, label: "ממתין לתגובה" };
          }
        }
      }

      if (currentUser.role === "referent") {
        // Referent review pending response
        if (actionableLessonIds.has(lessonId)) {
          const pendingReviews = (referentReviews || []).filter(
            (r) => r.lessonId === lessonId && r.referentId === currentUser.id && !r.respondedAt
          );
          if (pendingReviews.length > 0) {
            return { needed: true, label: "ממתין לתגובה" };
          }
        }
      }

      return { needed: false, label: "" };
    };
  }, [lessons, implementations, currentUser, actionableLessonIds, referentReviews]);

  const projectNeedsAction = useMemo(() => {
    return (projectId: number): ProjectActionInfo => {
      if (currentUser.role === "project_manager") {
        const myProjects = currentUser.assignedProjects;
        if (!myProjects.includes(projectId)) {
          // Also check if manager
          const project = projects.find((p) => p.id === projectId);
          if (!project || project.managerId !== currentUser.id) {
            return { needed: false, count: 0, label: "" };
          }
        }
        const pendingCount = implementations.filter(
          (impl) =>
            impl.projectId === projectId &&
            !impl.respondedBy &&
            actionableLessonIds.has(impl.lessonId)
        ).length;
        if (pendingCount > 0) {
          return { needed: true, count: pendingCount, label: `${pendingCount} לקחים ממתינים לתגובה` };
        }
      }

      if (currentUser.role === "admin") {
        // Check for new lessons in this project
        const newLessonsCount = lessons.filter(
          (l) => l.projectId === projectId && l.status === "new"
        ).length;
        if (newLessonsCount > 0) {
          return { needed: true, count: newLessonsCount, label: `${newLessonsCount} לקחים ממתינים לאישור` };
        }
      }

      return { needed: false, count: 0, label: "" };
    };
  }, [implementations, lessons, currentUser, projects, actionableLessonIds]);

  const totalActionCount = useMemo(() => {
    let count = 0;
    if (currentUser.role === "admin") {
      count += lessons.filter((l) => l.status === "new").length;
    }
    if (currentUser.role === "project_manager") {
      count += lessons.filter((l) => l.status === "rejected" && l.createdBy === currentUser.id).length;
      const myProjectIds = currentUser.assignedProjects;
      count += implementations.filter(
        (impl) =>
          !impl.respondedBy &&
          myProjectIds.includes(impl.projectId) &&
          actionableLessonIds.has(impl.lessonId)
      ).length;
    }
    if (currentUser.role === "referent") {
      count += (referentReviews || []).filter(
        (r) => r.referentId === currentUser.id && !r.respondedAt && actionableLessonIds.has(r.lessonId)
      ).length;
    }
    return count;
  }, [lessons, implementations, currentUser, actionableLessonIds, referentReviews]);

  const lessonsActionCount = useMemo(() => {
    if (currentUser.role === "admin") {
      return lessons.filter((l) => l.status === "new").length;
    }
    if (currentUser.role === "project_manager") {
      return lessons.filter((l) => l.status === "rejected" && l.createdBy === currentUser.id).length;
    }
    if (currentUser.role === "referent") {
      return (referentReviews || []).filter(
        (r) => r.referentId === currentUser.id && !r.respondedAt && actionableLessonIds.has(r.lessonId)
      ).length;
    }
    return 0;
  }, [lessons, currentUser, referentReviews, actionableLessonIds]);

  const projectsActionCount = useMemo(() => {
    if (currentUser.role === "project_manager") {
      const myProjectIds = currentUser.assignedProjects;
      return implementations.filter(
        (impl) =>
          !impl.respondedBy &&
          myProjectIds.includes(impl.projectId) &&
          actionableLessonIds.has(impl.lessonId)
      ).length;
    }
    if (currentUser.role === "admin") {
      return lessons.filter((l) => l.status === "new" && l.projectId !== null).length;
    }
    return 0;
  }, [lessons, implementations, currentUser, actionableLessonIds]);

  return { lessonNeedsAction, projectNeedsAction, totalActionCount, lessonsActionCount, projectsActionCount };
};
