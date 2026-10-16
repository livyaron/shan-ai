import { useCallback } from "react";
import { useUser } from "@/context/UserContext";
import {
  ADMIN_FINAL_ACTIONS,
  creatorCompletionTarget,
  referentDecisionNext,
  type AdminFinalAction,
} from "@/lib/lessonActions";

/**
 * The lesson workflow steps as single calls — the computer's lesson page and the
 * phone both use these, so "what happens after a referent decides" lives once
 * (lib/lessonActions.ts) and is never re-spelled in a button.
 */
export const useLessonWorkflow = () => {
  const { updateReferentDecision, updateWorkflowStatus, returnLessonToCreator } = useUser();

  /** The referent's decision + response; returns the workflow status it moved to. */
  const sendReferentDecision = useCallback(async (lessonId: number, reviewId: number, decision: string, text: string) => {
    await updateReferentDecision(reviewId, decision, text);
    const next = referentDecisionNext(decision);
    await updateWorkflowStatus(lessonId, next);
    return next;
  }, [updateReferentDecision, updateWorkflowStatus]);

  /** "סיימתי להשלים" — back to whoever asked; returns where it went. */
  const completeAsCreator = useCallback(async (lesson: { id: number; returnedBy?: string }, note: string) => {
    const to = creatorCompletionTarget(lesson);
    await updateWorkflowStatus(lesson.id, to, note.trim() || "היוצר סימן שהשלים את הנדרש", "creator_completed");
    return to;
  }, [updateWorkflowStatus]);

  const finalizeAsAdmin = useCallback(
    (lessonId: number, action: AdminFinalAction, note: string) => updateWorkflowStatus(lessonId, ADMIN_FINAL_ACTIONS[action], note),
    [updateWorkflowStatus],
  );

  return { sendReferentDecision, completeAsCreator, finalizeAsAdmin, returnToCreator: returnLessonToCreator };
};
