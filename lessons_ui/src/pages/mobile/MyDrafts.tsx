import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Smartphone } from "lucide-react";
import { useUser } from "@/context/UserContext";
import { Button } from "@/components/ui/button";
import { isDraft, missingForSubmit } from "@/lib/lessonAssignment";
import { FIELD_NAMES, hasContent, loadLocalDraft } from "@/lib/lessonDraft";
import { ilDate } from "@/lib/ilDate";

// הטיוטות שלי (PLAN-lessons-mobile.md M1): the creator's server drafts, plus a
// lesson typed on this phone that never reached the server. Tap = the wizard.

const MyDrafts = () => {
  const navigate = useNavigate();
  const { currentUser, users, lessons } = useUser();

  const drafts = useMemo(
    () => lessons.filter((l) => isDraft(l) && l.createdBy === currentUser.id).sort((a, b) => b.id - a.id),
    [lessons, currentUser.id],
  );
  const unsent = useMemo(() => {
    const local = loadLocalDraft(currentUser.id, null);
    return local && hasContent(local.form) ? local : null;
  }, [currentUser.id]);

  return (
    <div className="min-h-[100dvh] flex flex-col">
      <header className="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border px-4 py-3 flex items-center justify-between">
        <h1 className="font-heading font-bold text-lg">הטיוטות שלי</h1>
        <Button size="sm" className="gap-1" onClick={() => navigate("/m/new")}>
          <Plus className="w-4 h-4" /> לקח חדש
        </Button>
      </header>

      <main className="flex-1 px-4 py-4 space-y-3 pb-28">
        {unsent && (
          <Link to="/m/new" className="block rounded-xl border-2 border-warning/60 bg-warning/5 p-4 space-y-1">
            <p className="flex items-center gap-2 text-xs font-medium text-warning">
              <Smartphone className="w-4 h-4" /> שמור רק בטלפון — עוד לא נשלח
            </p>
            <p className="font-medium">{unsent.form.title || "(ללא כותרת)"}</p>
            <p className="text-xs text-muted-foreground">התחלת ב-{new Date(unsent.savedAt).toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" })}</p>
          </Link>
        )}

        {drafts.map((l) => {
          const missing = missingForSubmit(l, users);
          return (
            <Link key={l.id} to={`/m/lessons/${l.id}/edit`} className="block rounded-xl border border-border bg-card p-4 space-y-1 active:bg-muted">
              <p className="font-medium">{l.title || "(ללא כותרת)"}</p>
              <p className="text-xs text-muted-foreground">{l.project || "ללא פרויקט"} · {ilDate(l.date)}</p>
              <p className={missing.length ? "text-xs text-muted-foreground" : "text-xs text-success font-medium"}>
                {missing.length ? `חסר לשליחה: ${missing.map((f) => FIELD_NAMES[f]).join(", ")}` : "✓ מוכן לשליחה לאישור"}
              </p>
            </Link>
          );
        })}

        {!unsent && drafts.length === 0 && (
          <p className="text-center text-muted-foreground py-12">אין טיוטות. כל הלקחים שלך נשלחו.</p>
        )}
      </main>

    </div>
  );
};

export default MyDrafts;
