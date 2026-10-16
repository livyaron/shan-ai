import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bell, FileEdit, LogOut, Monitor, Plus } from "lucide-react";
import { useUser, roleLabels } from "@/context/UserContext";
import { Button } from "@/components/ui/button";
import LessonCard from "@/components/mobile/LessonCard";
import { useMyTasks } from "@/hooks/useMyTasks";
import { isDraft } from "@/lib/lessonAssignment";
import { hasContent, loadLocalDraft } from "@/lib/lessonDraft";
import { myScope } from "@/lib/lessonScope";
import { setPrefersDesktop } from "@/lib/standalone";

const RECENT = 5;

/** The phone's home (PLAN-lessons-mobile.md M2): add a lesson, what waits for me, my latest. */
const MobileHome = () => {
  const navigate = useNavigate();
  const { currentUser, lessons, unreadCount, hasPermission, logout } = useUser();
  const taskCount = useMyTasks().length;
  const canAdd = hasPermission("add_lessons");

  const draftCount = useMemo(() => {
    const server = lessons.filter((l) => isDraft(l) && l.createdBy === currentUser.id).length;
    const local = loadLocalDraft(currentUser.id, null);
    return server + (local && hasContent(local.form) ? 1 : 0);
  }, [lessons, currentUser.id]);

  const recent = useMemo(
    () => myScope(lessons, currentUser).filter((l) => !isDraft(l)).sort((a, b) => b.id - a.id).slice(0, RECENT),
    [lessons, currentUser],
  );

  const desktop = () => {
    setPrefersDesktop(true);
    navigate("/");
  };

  return (
    <div className="pb-24">
      <header className="px-4 pt-5 pb-4 bg-primary text-primary-foreground space-y-1">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm opacity-80">שלום,</p>
            <h1 className="font-heading text-xl font-bold">{currentUser.name}</h1>
            <p className="text-xs opacity-70">{roleLabels[currentUser.role]}</p>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="text-primary-foreground" aria-label="התראות" onClick={() => navigate("/notifications")}>
              <span className="relative">
                <Bell className="w-5 h-5" />
                {unreadCount > 0 && (
                  <span className="absolute -top-1.5 -left-1.5 min-w-4 h-4 px-1 rounded-full bg-destructive text-[10px] leading-4 text-destructive-foreground">{unreadCount}</span>
                )}
              </span>
            </Button>
            <Button variant="ghost" size="icon" className="text-primary-foreground" aria-label="יציאה" onClick={logout}>
              <LogOut className="w-5 h-5" />
            </Button>
          </div>
        </div>
      </header>

      <main className="px-4 py-4 space-y-4">
        {canAdd && (
          <Button className="w-full h-16 text-lg gap-2 bg-accent text-accent-foreground hover:bg-accent/90" onClick={() => navigate("/m/new")}>
            <Plus className="w-6 h-6" /> לקח חדש
          </Button>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Link to="/m/tasks" className="rounded-xl border border-border bg-card p-4 active:bg-muted">
            <p className="text-3xl font-bold">{taskCount}</p>
            <p className="text-sm text-muted-foreground">ממתינים לי</p>
          </Link>
          {canAdd ? (
            <Link to="/m/drafts" className="rounded-xl border border-border bg-card p-4 active:bg-muted">
              <p className="text-3xl font-bold flex items-center gap-2">{draftCount}<FileEdit className="w-5 h-5 text-muted-foreground" /></p>
              <p className="text-sm text-muted-foreground">הטיוטות שלי</p>
            </Link>
          ) : <span />}
        </div>

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">הלקחים האחרונים שלי</h2>
            <Link to="/m/lessons" className="text-sm text-primary">הכול</Link>
          </div>
          {recent.map((l) => <LessonCard key={l.id} lesson={l} />)}
          {recent.length === 0 && <p className="text-sm text-muted-foreground py-4 text-center">עוד אין לקחים.</p>}
        </section>

        <button type="button" onClick={desktop} className="w-full flex items-center justify-center gap-2 py-3 text-sm text-muted-foreground">
          <Monitor className="w-4 h-4" /> תצוגת מחשב
        </button>
      </main>
    </div>
  );
};

export default MobileHome;
