import { useMemo } from "react";
import { useUser } from "@/context/UserContext";
import LessonCard from "@/components/mobile/LessonCard";
import { useMyTasks } from "@/hooks/useMyTasks";
import { TASK_LABELS, type TaskKind } from "@/lib/lessonActions";

// המשימות שלי (PLAN-lessons-mobile.md M3): everything waiting for me, by kind.
// Tap a lesson → its page, where the action panel does the work.

const ORDER: TaskKind[] = ["approve_new", "admin_final", "referent_decide", "referent_feedback", "pm_feedback", "creator_complete"];

const MyTasks = () => {
  const { lessons, projects } = useUser();
  const tasks = useMyTasks();

  const groups = useMemo(
    () => ORDER.map((kind) => ({ kind, items: tasks.filter((t) => t.kind === kind) })).filter((g) => g.items.length > 0),
    [tasks],
  );

  return (
    <div className="pb-24">
      <header className="sticky top-0 z-20 bg-background/95 backdrop-blur border-b border-border px-4 py-3">
        <h1 className="font-heading font-bold text-lg">המשימות שלי</h1>
        <p className="text-xs text-muted-foreground">{tasks.length ? `${tasks.length} ממתינות לך` : "אין משימות פתוחות"}</p>
      </header>
      <main className="px-4 py-4 space-y-6">
        {groups.map((g) => (
          <section key={g.kind} className="space-y-2">
            <h2 className="font-semibold">{TASK_LABELS[g.kind]} ({g.items.length})</h2>
            {g.items.map((t) => {
              const lesson = lessons.find((l) => l.id === t.lessonId);
              if (!lesson) return null;
              const project = t.projectId != null ? projects.find((p) => p.id === t.projectId)?.name : undefined;
              return (
                <LessonCard
                  key={`${t.kind}-${t.lessonId}-${t.rowId ?? ""}`}
                  lesson={lesson}
                  action={project ? `${TASK_LABELS[t.kind]} · ${project}` : TASK_LABELS[t.kind]}
                />
              );
            })}
          </section>
        ))}
        {tasks.length === 0 && <p className="text-center text-muted-foreground py-12">✓ הכול מטופל. אין דבר שממתין לך.</p>}
      </main>
    </div>
  );
};

export default MyTasks;
