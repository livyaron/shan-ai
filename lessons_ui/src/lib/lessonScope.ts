/**
 * Which lessons a user sees by default, and when "all lessons" is on — the one
 * rule for the computer's lessons page and the phone list (PLAN-lessons-mobile.md M2).
 *
 * - admin / viewer: the whole organisation.
 * - referent: lessons distributed to them, plus their own.
 * - everyone else: lessons they created.
 * "הכול" widens it to every lesson; a viewer always sees all and has no toggle.
 * Drafts of other users never arrive here — the provider already filtered them.
 */

interface UserLike {
  id: string;
  role: string;
}

interface LessonLike {
  id: number;
  createdBy: string;
  distributedToReferents?: string[];
}

export const seesEverything = (user: UserLike): boolean => user.role === "admin" || user.role === "viewer";

/** A viewer has nothing to toggle; an admin already sees everything. */
export const canToggleScope = (user: UserLike): boolean => !seesEverything(user);

export const myScope = <L extends LessonLike>(lessons: L[], user: UserLike): L[] => {
  if (seesEverything(user)) return lessons;
  if (user.role === "referent") {
    return lessons.filter((l) => l.distributedToReferents?.includes(user.id) || l.createdBy === user.id);
  }
  return lessons.filter((l) => l.createdBy === user.id);
};

export const scopedLessons = <L extends LessonLike>(lessons: L[], user: UserLike, showAll: boolean): L[] =>
  showAll && user.role !== "viewer" ? lessons : myScope(lessons, user);
