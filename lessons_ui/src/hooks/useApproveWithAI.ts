import { useCallback, useState } from "react";
import { useUser, PROJECT_STAGES, projectTypeLabels, stationTypeLabels } from "@/context/UserContext";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "@/hooks/use-toast";
import { aiDistributionTargets } from "@/lib/lessonActions";

export interface PostApproveReview {
  recommended_projects: { project_name: string; reason: string; priority: string }[];
  recommended_referents: { referent_id: string; referent_name: string; reason: string }[];
  implementation_steps: string[];
  attention_points: string[];
  summary: string;
}

/**
 * Admin approval of a new lesson, then the AI's distribution proposal — the
 * computer's lessons page and the phone run this same flow (PLAN-lessons-mobile.md M3).
 * The referent is the creator's choice alone; the AI recommends projects only.
 */
export const useApproveWithAI = () => {
  const { lessons, projects, equipment, approveLesson, updateLessonAIReview, distributeLesson } = useUser();
  const [lessonId, setLessonId] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [isReviewing, setIsReviewing] = useState(false);
  const [review, setReview] = useState<PostApproveReview | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [filteredOut, setFilteredOut] = useState<string[]>([]);

  const close = useCallback(() => {
    setOpen(false);
    setLessonId(null);
    setReview(null);
    setSelected([]);
    setFilteredOut([]);
  }, []);

  /** Approves; then asks the AI. Returns false when the approval itself failed. */
  const approve = useCallback(async (id: number): Promise<boolean> => {
    try {
      await approveLesson(id);
    } catch (err) {
      console.error("Approve error:", err);
      toast({ title: "האישור נכשל", description: "הלקח לא שונה — נסה שוב", variant: "destructive" });
      return false;
    }

    setLessonId(id);
    setIsReviewing(true);
    setReview(null);
    setOpen(true);
    try {
      const lesson = lessons.find((l) => l.id === id);
      if (!lesson) return true;
      const lessonEquipment = equipment.filter((eq) => lesson.equipmentIds.includes(eq.id));
      const lessonStageIndex = PROJECT_STAGES.indexOf(lesson.stage);
      const activeProjects = projects
        .filter((p) => p.id !== lesson.projectId)
        .map((p) => ({
          id: p.id,
          name: p.name,
          stage: PROJECT_STAGES[p.stageIndex],
          type: projectTypeLabels[p.projectType],
          station: stationTypeLabels[p.stationType],
          equipment: equipment.filter((eq) => p.equipmentIds.includes(eq.id)).map((eq) => eq.name),
        }));

      const { data, error } = await supabase.functions.invoke("review-lesson", {
        body: {
          type: "post_approve",
          lesson: {
            title: lesson.title,
            description: lesson.description,
            projectName: lesson.project,
            stage: lesson.stage,
            category: lesson.category,
            risk: lesson.risk,
            equipmentNames: lessonEquipment.map((eq) => eq.name),
          },
          projects: activeProjects,
          referents: [],
        },
      });
      if (error) throw error;
      const result = (data?.review as PostApproveReview | null) ?? null;
      setReview(result);
      if (result) {
        updateLessonAIReview(id, result);
        const targets = aiDistributionTargets(result.recommended_projects, projects, lessonStageIndex);
        setSelected(targets.selected);
        setFilteredOut(targets.filteredOut);
      } else {
        setSelected([]);
        setFilteredOut([]);
      }
    } catch (e) {
      console.error("Post-approve AI error:", e);
      toast({ title: "הלקח אושר", description: "אושר בהצלחה אך ניתוח AI נכשל — ניתן להפיץ ידנית", variant: "destructive" });
      close();
    } finally {
      setIsReviewing(false);
    }
    return true;
  }, [approveLesson, lessons, equipment, projects, updateLessonAIReview, close]);

  const toggle = useCallback((projectId: number) => {
    setSelected((prev) => (prev.includes(projectId) ? prev.filter((x) => x !== projectId) : [...prev, projectId]));
  }, []);

  /** Distributes to the selected projects and closes. */
  const distribute = useCallback(async () => {
    if (lessonId == null || selected.length === 0) return;
    const count = selected.length;
    await distributeLesson(lessonId, selected, [], "ai");
    toast({ title: "לקח הופץ בהצלחה ✓", description: `הופץ ל-${count} פרויקטים` });
    close();
  }, [lessonId, selected, distributeLesson, close]);

  return { lessonId, open, isReviewing, review, selected, filteredOut, approve, toggle, distribute, close };
};
