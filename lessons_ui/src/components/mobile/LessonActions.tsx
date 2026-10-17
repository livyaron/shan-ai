import { useState } from "react";
import { Link } from "react-router-dom";
import { Check, Loader2, MessageSquare, RotateCcw, Send, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useUser, type Lesson } from "@/context/UserContext";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import ChoiceChips from "@/components/mobile/ChoiceChips";
import { useApproveWithAI } from "@/hooks/useApproveWithAI";
import { useLessonWorkflow } from "@/hooks/useLessonWorkflow";
import { useMyTasks } from "@/hooks/useMyTasks";
import { toast } from "@/hooks/use-toast";
import { REFERENT_DECISIONS, RETURN_REASONS, priorityLabels } from "@/lib/constants";
import { TASK_LABELS, WF_IN_REFERENT, WF_REJECTED, type LessonTask } from "@/lib/lessonActions";
import { cn } from "@/lib/utils";

// The phone's action panel on a lesson (PLAN-lessons-mobile.md M3): exactly the
// things this user has to do here, each through the same call the computer uses.

const failed = (e: unknown) => {
  console.error("Lesson action error:", e);
  toast({ title: "הפעולה נכשלה", description: "לא נשמר — בדוק קליטה ונסה שוב", variant: "destructive" });
};

type Approval = ReturnType<typeof useApproveWithAI>;

const LessonActions = ({ lesson }: { lesson: Lesson }) => {
  const tasks = useMyTasks().filter((t) => t.lessonId === lesson.id);
  // Held here, not in the approve card: approving ends that task, and the AI's
  // distribution sheet must outlive it.
  const approval = useApproveWithAI();
  return (
    <>
      {tasks.length > 0 && (
        <div className="space-y-4">
          {tasks.map((t) => (
            <section key={`${t.kind}-${t.rowId ?? ""}`} className="rounded-xl border-2 border-primary/40 bg-primary/5 p-4 space-y-3">
              <h2 className="font-semibold">{TASK_LABELS[t.kind]}</h2>
              <TaskBody task={t} lesson={lesson} approval={approval} />
            </section>
          ))}
        </div>
      )}
      {approval.open && <DistributionSheet lesson={lesson} approval={approval} />}
    </>
  );
};

const TaskBody = ({ task, lesson, approval }: { task: LessonTask; lesson: Lesson; approval: Approval }) => {
  switch (task.kind) {
    case "approve_new":
      return <ApproveNew lesson={lesson} approval={approval} />;
    case "admin_final":
      return <AdminFinal lesson={lesson} />;
    case "referent_decide":
      return <ReferentDecide lesson={lesson} reviewId={task.rowId!} />;
    case "referent_feedback":
    case "pm_feedback":
      return <Feedback task={task} />;
    case "creator_complete":
      return <CreatorComplete lesson={lesson} />;
  }
};

// ---------------------------------------------------------------- admin: a new lesson

export const ApproveNew = ({ lesson, approval }: { lesson: Lesson; approval: Approval }) => {
  const { rejectLesson } = useUser();
  const [busy, setBusy] = useState(false);
  const [confirmReject, setConfirmReject] = useState(false);

  const approve = async () => {
    setBusy(true);
    const ok = await approval.approve(lesson.id);
    setBusy(false);
    if (ok) toast({ title: "הלקח אושר ✓", description: lesson.assignedTo ? "הועבר לרפרנט שבחר יוצר הלקח" : undefined });
  };
  const reject = async () => {
    setBusy(true);
    try {
      await rejectLesson(lesson.id);
      toast({ title: "הלקח נדחה" });
    } catch (e) {
      failed(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        <Button className="h-12 gap-2 bg-success text-success-foreground hover:bg-success/90" disabled={busy} onClick={approve}>
          {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <Check className="w-5 h-5" />} אשר
        </Button>
        {confirmReject ? (
          <Button variant="destructive" className="h-12" disabled={busy} onClick={reject}>בטוח? דחה</Button>
        ) : (
          <Button variant="outline" className="h-12 gap-2 border-destructive text-destructive" disabled={busy} onClick={() => setConfirmReject(true)}>
            <X className="w-5 h-5" /> דחה
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">אחרי האישור ה-AI יציע פרויקטים להפצה.</p>

    </>
  );
};

/** The AI's distribution proposal after approval — full screen on the phone, a centred panel on the computer. */
export const DistributionSheet = ({ lesson, approval }: { lesson: Lesson; approval: Approval }) => {
  const { projects } = useUser();
  return (
    <div role="dialog" aria-label="המלצות הפצה" className="fixed inset-0 z-50 bg-background flex flex-col md:inset-x-0 md:inset-y-8 md:mx-auto md:max-w-2xl md:rounded-xl md:border md:border-border md:shadow-2xl">
      <header className="px-4 py-3 border-b border-border">
        <h2 className="font-heading font-bold text-lg">המלצות AI להפצה</h2>
        <p className="text-xs text-muted-foreground truncate">{lesson.title}</p>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {approval.isReviewing && (
          <div className="flex flex-col items-center gap-3 py-12">
            <Loader2 className="w-8 h-8 animate-spin text-accent" />
            <p className="text-sm text-muted-foreground">ה-AI מנתח את הלקח ומציע פרויקטים...</p>
          </div>
        )}
        {approval.review && (
          <>
            <p className="text-sm text-muted-foreground">{approval.review.summary}</p>
            <ul className="space-y-2">
              {approval.review.recommended_projects.map((rp, i) => {
                const id = projects.find((p) => p.name === rp.project_name)?.id;
                const on = id != null && approval.selected.includes(id);
                const blocked = approval.filteredOut.includes(rp.project_name);
                return (
                  <li key={i}>
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={on}
                      disabled={id == null}
                      onClick={() => id != null && approval.toggle(id)}
                      className={cn("w-full text-right rounded-lg border p-3 space-y-1", on ? "border-primary bg-primary/10" : "border-border")}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="font-medium">{rp.project_name}</span>
                        {on && <Check className="w-4 h-4 text-primary" />}
                      </span>
                      <span className="block text-xs text-muted-foreground">{priorityLabels[rp.priority] ?? rp.priority} · {rp.reason}</span>
                      {blocked && <span className="block text-xs text-destructive">לא סומן אוטומטית: הפרויקט לא באותו שלב של הלקח.</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
            {approval.review.recommended_projects.length === 0 && <p className="text-sm text-muted-foreground">ה-AI לא מצא פרויקטים להפצה.</p>}
          </>
        )}
      </div>
      <footer className="px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] border-t border-border flex gap-2">
        {approval.selected.length > 0 && !approval.isReviewing && (
          <Button className="h-12 flex-1 gap-2" onClick={() => void approval.distribute()}>
            <Send className="w-4 h-4" /> הפץ ({approval.selected.length})
          </Button>
        )}
        <Button variant="outline" className="h-12 flex-1" onClick={approval.close}>
          {approval.selected.length > 0 ? "דלג" : "סגור"}
        </Button>
      </footer>
    </div>
  );
};

// ---------------------------------------------------------------- admin: after the referent

const AdminFinal = ({ lesson }: { lesson: Lesson }) => {
  const { referentReviews, users } = useUser();
  const workflow = useLessonWorkflow();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [returning, setReturning] = useState(false);
  const decisions = referentReviews.filter((r) => r.lessonId === lesson.id && r.decision);

  const act = async (action: "approve" | "reject" | "toReferent") => {
    setBusy(true);
    try {
      await workflow.finalizeAsAdmin(lesson.id, action, note);
      toast({ title: action === "approve" ? "הלקח אושר והופץ ✓" : action === "reject" ? "הלקח נדחה" : "הלקח הוחזר לרפרנט" });
    } catch (e) {
      failed(e);
    } finally {
      setBusy(false);
    }
  };

  if (returning) return <ReturnToCreator lessonId={lesson.id} onDone={() => setReturning(false)} />;
  return (
    <>
      {decisions.map((r) => (
        <div key={r.id} className="rounded-lg bg-background p-3 text-sm space-y-1">
          <p><span className="font-medium">{users.find((u) => u.id === r.referentId)?.name}</span> · {r.decision}</p>
          {r.responseText && <p className="text-muted-foreground whitespace-pre-wrap">{r.responseText}</p>}
        </div>
      ))}
      <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="הערות (אופציונלי)" rows={2} className="text-base" />
      <div className="grid grid-cols-2 gap-2">
        <Button className="h-12 bg-success text-success-foreground hover:bg-success/90" disabled={busy} onClick={() => act("approve")}>אשר והפץ</Button>
        <Button variant="outline" className="h-12 border-destructive text-destructive" disabled={busy} onClick={() => act("reject")}>דחה</Button>
        <Button variant="outline" className="h-12 border-warning text-warning" disabled={busy} onClick={() => act("toReferent")}>החזר לרפרנט</Button>
        <Button variant="outline" className="h-12 gap-1" disabled={busy} onClick={() => setReturning(true)}>
          <RotateCcw className="w-4 h-4" /> החזר ליוצר
        </Button>
      </div>
    </>
  );
};

// ---------------------------------------------------------------- referent decision

const ReferentDecide = ({ lesson, reviewId }: { lesson: Lesson; reviewId: number }) => {
  const workflow = useLessonWorkflow();
  const [decision, setDecision] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [returning, setReturning] = useState(false);

  const send = async () => {
    setBusy(true);
    try {
      const next = await workflow.sendReferentDecision(lesson.id, reviewId, decision, text);
      toast({ title: next === WF_REJECTED ? "הלקח נדחה" : "הלקח הועבר לאישור מנהל מערכת ✓" });
    } catch (e) {
      failed(e);
    } finally {
      setBusy(false);
    }
  };

  if (returning) return <ReturnToCreator lessonId={lesson.id} onDone={() => setReturning(false)} />;
  return (
    <>
      <p className="text-sm font-medium">החלטה</p>
      <ChoiceChips label="החלטת רפרנט" options={REFERENT_DECISIONS.map((d) => ({ value: d, label: d }))} value={decision} onChange={(v) => setDecision(v === decision ? "" : v)} />
      <p className="text-sm font-medium">התייחסות</p>
      <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} className="text-base"
        placeholder="מה נבדק? מה נעשה? האם נדרש טיפול נוסף?" />
      <div className="flex gap-2">
        <Button className="h-12 flex-[2] gap-2" disabled={busy || !decision || !text.trim()} onClick={send}>
          {busy ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-4 h-4" />} שלח התייחסות
        </Button>
        <Button variant="outline" className="h-12 flex-1 gap-1" disabled={busy} onClick={() => setReturning(true)}>
          <RotateCcw className="w-4 h-4" /> החזר ליוצר
        </Button>
      </div>
    </>
  );
};

// ---------------------------------------------------------------- return to creator

const ReturnToCreator = ({ lessonId, onDone }: { lessonId: number; onDone: () => void }) => {
  const workflow = useLessonWorkflow();
  const [reason, setReason] = useState("");
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = reason && (reason !== "אחר" || custom.trim());

  const send = async () => {
    setBusy(true);
    try {
      await workflow.returnToCreator(lessonId, reason, custom.trim() || undefined);
      toast({ title: "הלקח הוחזר ליוצר להשלמה ✓" });
      onDone();
    } catch (e) {
      failed(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p className="text-sm font-medium">למה להחזיר ליוצר?</p>
      <ChoiceChips label="סיבת החזרה" options={RETURN_REASONS.map((r) => ({ value: r, label: r }))} value={reason} onChange={(v) => setReason(v === reason ? "" : v)} />
      {reason === "אחר" && (
        <Textarea value={custom} onChange={(e) => setCustom(e.target.value)} rows={2} className="text-base" placeholder="פרט את הסיבה" />
      )}
      <div className="flex gap-2">
        <Button className="h-12 flex-[2]" disabled={busy || !ready} onClick={send}>החזר ליוצר</Button>
        <Button variant="ghost" className="h-12 flex-1" disabled={busy} onClick={onDone}>ביטול</Button>
      </div>
    </>
  );
};

// ---------------------------------------------------------------- referent / project feedback

const Feedback = ({ task }: { task: LessonTask }) => {
  const { respondToImplementation, respondToReferentReview, projects } = useUser();
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const project = task.projectId != null ? projects.find((p) => p.id === task.projectId)?.name : undefined;

  const respond = async (isRelevant: boolean, isImplemented: boolean) => {
    if (task.rowId == null) return;
    setBusy(true);
    try {
      const response = { isRelevant, isImplemented, notes };
      if (task.kind === "pm_feedback") await respondToImplementation(task.rowId, response);
      else await respondToReferentReview(task.rowId, response);
      toast({ title: "תגובה נשמרה ✓" });
    } catch (e) {
      failed(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {project && <p className="text-sm">פרויקט: <span className="font-medium">{project}</span></p>}
      <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="הערות (אופציונלי)" rows={2} className="text-base" />
      <div className="grid gap-2">
        <Button className="h-12 gap-2 bg-success text-success-foreground hover:bg-success/90" disabled={busy} onClick={() => respond(true, true)}>
          <ThumbsUp className="w-4 h-4" /> רלוונטי ויושם
        </Button>
        <Button variant="outline" className="h-12 gap-2 border-info text-info" disabled={busy} onClick={() => respond(true, false)}>
          <MessageSquare className="w-4 h-4" /> רלוונטי, לא יושם
        </Button>
        <Button variant="outline" className="h-12 gap-2 border-destructive text-destructive" disabled={busy} onClick={() => respond(false, false)}>
          <ThumbsDown className="w-4 h-4" /> לא רלוונטי
        </Button>
      </div>
    </>
  );
};

// ---------------------------------------------------------------- creator: complete and return

const CreatorComplete = ({ lesson }: { lesson: Lesson }) => {
  const workflow = useLessonWorkflow();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const done = async () => {
    setBusy(true);
    try {
      const to = await workflow.completeAsCreator(lesson, note);
      toast({ title: "הלקח הוחזר לטיפול ✓", description: to === WF_IN_REFERENT ? "הועבר חזרה לרפרנט" : "הועבר חזרה למנהל המערכת" });
    } catch (e) {
      failed(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {lesson.returnReason && (
        <p className="text-sm rounded-lg bg-warning/10 p-3"><span className="font-medium">סיבת ההחזרה: </span>{lesson.returnReason}</p>
      )}
      <Link to={`/lessons/${lesson.id}`} className="block text-sm text-primary underline">עריכת הלקח (בתצוגה המלאה)</Link>
      <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="text-base" placeholder="מה השלמת? (יישמר בהיסטוריה)" />
      <Button className="h-12 w-full gap-2 bg-success text-success-foreground hover:bg-success/90" disabled={busy} onClick={done}>
        <Check className="w-4 h-4" /> סיימתי להשלים
      </Button>
    </>
  );
};

export default LessonActions;
