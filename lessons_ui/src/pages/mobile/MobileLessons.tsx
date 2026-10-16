import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search, X } from "lucide-react";
import { useUser } from "@/context/UserContext";
import { Input } from "@/components/ui/input";
import LessonCard from "@/components/mobile/LessonCard";
import { useMyTasks } from "@/hooks/useMyTasks";
import { TASK_LABELS } from "@/lib/lessonActions";
import { canToggleScope, scopedLessons } from "@/lib/lessonScope";
import { cn } from "@/lib/utils";

// הלקחים בטלפון (PLAN-lessons-mobile.md M2): search, project, "mine / all",
// "waiting for me" — cards instead of the table, more as you scroll.

const PAGE = 30;

const MobileLessons = () => {
  const [params, setParams] = useSearchParams();
  const { currentUser, lessons, projects } = useUser();
  const tasks = useMyTasks();
  // "Waiting for me" = my tasks (lib/lessonActions.ts) — the same count as the home and the tasks tab.
  const taskLabel = useMemo(() => {
    const m = new Map<number, string>();
    for (const t of tasks) if (!m.has(t.lessonId)) m.set(t.lessonId, TASK_LABELS[t.kind]);
    return m;
  }, [tasks]);

  const [query, setQuery] = useState(params.get("q") ?? "");
  const projectId = params.get("project") ?? "";
  const showAll = params.get("all") === "1";
  const actionOnly = params.get("action") === "1";
  const [shown, setShown] = useState(PAGE);
  const sentinel = useRef<HTMLDivElement>(null);

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const list = useMemo(() => {
    const q = query.trim();
    return scopedLessons(lessons, currentUser, showAll)
      .filter((l) => !projectId || String(l.projectId) === projectId)
      .filter((l) => !q || l.title.includes(q) || l.project.includes(q) || l.description.includes(q))
      .map((l) => ({ lesson: l, action: taskLabel.get(l.id) }))
      .filter((x) => !actionOnly || x.action)
      // Same order as the computer's list: what waits for me first, then newest.
      .sort((a, b) => Number(!!b.action) - Number(!!a.action) || b.lesson.date.localeCompare(a.lesson.date) || b.lesson.id - a.lesson.id);
  }, [lessons, currentUser, showAll, projectId, query, actionOnly, taskLabel]);

  const projectOptions = useMemo(() => {
    const ids = new Set(scopedLessons(lessons, currentUser, showAll).map((l) => l.projectId).filter((x): x is number => x != null));
    return projects.filter((p) => ids.has(p.id)).sort((a, b) => a.name.localeCompare(b.name, "he"));
  }, [lessons, currentUser, showAll, projects]);

  useEffect(() => setShown(PAGE), [query, projectId, showAll, actionOnly]);

  // Load the next page when the end of the list scrolls into view.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || shown >= list.length) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setShown((n) => n + PAGE);
    }, { rootMargin: "400px" });
    io.observe(el);
    return () => io.disconnect();
  }, [shown, list.length]);

  return (
    <div className="pb-24">
      <header className="sticky top-0 z-20 bg-background/95 backdrop-blur border-b border-border px-4 pt-3 pb-3 space-y-3">
        <h1 className="font-heading font-bold text-lg">לקחים</h1>
        <div className="relative">
          <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onBlur={() => setParam("q", query.trim() || null)}
            placeholder="חיפוש בכותרת, פרויקט או תיאור"
            enterKeyHint="search"
            className="h-11 pr-9 text-base"
          />
          {query && (
            <button type="button" aria-label="נקה חיפוש" className="absolute left-2 top-1/2 -translate-y-1/2 p-1" onClick={() => { setQuery(""); setParam("q", null); }}>
              <X className="w-4 h-4 text-muted-foreground" />
            </button>
          )}
        </div>
        <div className="flex gap-2 overflow-x-auto -mx-4 px-4 pb-1">
          {canToggleScope(currentUser) && (
            <>
              <Pill on={!showAll} onClick={() => setParam("all", null)}>שלי</Pill>
              <Pill on={showAll} onClick={() => setParam("all", "1")}>הכול</Pill>
            </>
          )}
          <Pill on={actionOnly} onClick={() => setParam("action", actionOnly ? null : "1")}>ממתינים לי</Pill>
          <select
            aria-label="פרויקט"
            value={projectId}
            onChange={(e) => setParam("project", e.target.value || null)}
            className={cn("h-9 rounded-full border px-3 text-sm bg-card shrink-0 max-w-[12rem]", projectId ? "border-primary text-primary" : "border-border")}
          >
            <option value="">כל הפרויקטים</option>
            {projectOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </header>

      <main className="px-4 py-3 space-y-3">
        <p className="text-xs text-muted-foreground" aria-live="polite">{list.length} לקחים</p>
        {list.slice(0, shown).map(({ lesson, action }) => (
          <LessonCard key={lesson.id} lesson={lesson} action={action} />
        ))}
        {list.length === 0 && <p className="text-center text-muted-foreground py-12">לא נמצאו לקחים.</p>}
        <div ref={sentinel} />
      </main>
    </div>
  );
};

const Pill = ({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) => (
  <button
    type="button"
    aria-pressed={on}
    onClick={onClick}
    className={cn("h-9 px-4 rounded-full border text-sm shrink-0", on ? "bg-primary text-primary-foreground border-primary" : "bg-card border-border")}
  >
    {children}
  </button>
);

export default MobileLessons;
