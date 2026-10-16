import { useMemo } from "react";
import { useUser } from "@/context/UserContext";
import { pendingTasks, type LessonTask } from "@/lib/lessonActions";

/** What waits for the current user (lib/lessonActions.ts) — the phone's one "waiting for me". */
export const useMyTasks = (): LessonTask[] => {
  const { currentUser, lessons, referentReviews, implementations } = useUser();
  return useMemo(
    () => pendingTasks(currentUser, lessons, referentReviews, implementations),
    [currentUser, lessons, referentReviews, implementations],
  );
};
