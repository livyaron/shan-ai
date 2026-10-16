import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronRight, ExternalLink, FileText, Monitor, X } from "lucide-react";
import { useUser } from "@/context/UserContext";
import { Button } from "@/components/ui/button";
import LessonActions from "@/components/mobile/LessonActions";
import { riskColors, riskLabels, statusLabels } from "@/lib/constants";
import { isDraft } from "@/lib/lessonAssignment";
import { isImageUrl } from "@/lib/lessonFiles";
import { cn } from "@/lib/utils";
import { ilDate } from "@/lib/ilDate";

// A lesson on the phone, read only (PLAN-lessons-mobile.md M2). A draft of mine
// opens the wizard; anything that acts on a lesson stays on the full page for now.

const MobileLessonView = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { lessons, users, equipment, currentUser } = useUser();
  const [zoom, setZoom] = useState(false);

  const lesson = lessons.find((l) => l.id === Number(id));
  if (!lesson) {
    return (
      <div className="min-h-[60dvh] flex flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-lg">הלקח לא נמצא.</p>
        <Link to="/m/lessons" className="text-primary underline">לרשימת הלקחים</Link>
      </div>
    );
  }

  const name = (uid?: string) => (uid ? users.find((u) => u.id === uid)?.name ?? "" : "");
  const eqNames = equipment.filter((e) => lesson.equipmentIds.includes(e.id)).map((e) => e.name);
  const mineDraft = isDraft(lesson) && lesson.createdBy === currentUser.id;
  const back = () => (window.history.length > 1 ? navigate(-1) : navigate("/m/lessons"));

  return (
    <div className="pb-28">
      <header className="sticky top-0 z-20 bg-background/95 backdrop-blur border-b border-border px-2 py-2 flex items-center gap-1">
        <Button variant="ghost" size="icon" aria-label="חזרה" onClick={back}>
          <ChevronRight className="w-5 h-5" />
        </Button>
        <p className="text-sm text-muted-foreground">לקח #{lesson.id}</p>
      </header>

      <main className="px-4 py-4 space-y-5">
        <div className="space-y-2">
          <h1 className="font-heading text-xl font-bold leading-snug">{lesson.title}</h1>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className={cn("rounded-full border px-2 py-0.5", riskColors[lesson.risk])}>{riskLabels[lesson.risk] ?? lesson.risk}</span>
            <span className="rounded-full border border-border px-2 py-0.5">{statusLabels[lesson.status] ?? lesson.status}</span>
            {lesson.workflowStatus && lesson.workflowStatus !== statusLabels[lesson.status] && (
              <span className="rounded-full border border-border px-2 py-0.5">{lesson.workflowStatus}</span>
            )}
          </div>
        </div>

        {mineDraft && (
          <Button className="w-full h-12 text-base" onClick={() => navigate(`/m/lessons/${lesson.id}/edit`)}>
            ✏️ השלמה בשלבים
          </Button>
        )}

        <LessonActions lesson={lesson} />

        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm rounded-xl bg-muted/40 p-4">
          <Item label="פרויקט" value={lesson.project} wide />
          <Item label="שלב" value={lesson.stage} />
          <Item label="קטגוריה" value={lesson.category} />
          <Item label="תחום" value={lesson.professionalDomain} />
          <Item label="תאריך האירוע" value={ilDate(lesson.eventDate)} />
          <Item label="נוצר ע״י" value={name(lesson.createdBy)} />
          <Item label="רפרנט" value={name(lesson.assignedTo)} />
        </dl>

        <Section title="מה קרה" text={lesson.description} />
        <Section title="המלצה" text={lesson.recommendation} />

        {(lesson.impactScheduleDelay != null || lesson.impactBudgetCost != null || lesson.impactQualityDesc) && (
          <section className="space-y-1">
            <h2 className="font-semibold">השפעה</h2>
            <ul className="text-sm space-y-1">
              {lesson.impactScheduleDelay != null && <li>עיכוב בלו״ז: {lesson.impactScheduleDelay} ימים</li>}
              {lesson.impactBudgetCost != null && <li>עלות: {lesson.impactBudgetCost.toLocaleString("he-IL")} ₪</li>}
              {lesson.impactQualityDesc && <li className="whitespace-pre-wrap">איכות: {lesson.impactQualityDesc}</li>}
            </ul>
          </section>
        )}

        {eqNames.length > 0 && (
          <section className="space-y-2">
            <h2 className="font-semibold">ציוד / נושאים</h2>
            <div className="flex flex-wrap gap-2">
              {eqNames.map((n) => <span key={n} className="rounded-full bg-muted px-3 py-1 text-sm">{n}</span>)}
            </div>
          </section>
        )}

        {lesson.fileUrl && (
          <section className="space-y-2">
            <h2 className="font-semibold">קובץ מצורף</h2>
            {isImageUrl(lesson.fileUrl) ? (
              <button type="button" onClick={() => setZoom(true)} className="block w-full" aria-label="הגדל תמונה">
                <img src={lesson.fileUrl} alt="תמונה מצורפת" loading="lazy" className="w-full max-h-72 object-cover rounded-xl border border-border" />
              </button>
            ) : (
              <a href={lesson.fileUrl} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 rounded-xl border border-border p-3 text-primary">
                <FileText className="w-5 h-5" /> פתח קובץ <ExternalLink className="w-4 h-4 mr-auto" />
              </a>
            )}
          </section>
        )}

        <Link to={`/lessons/${lesson.id}`} className="flex items-center justify-center gap-2 py-3 text-sm text-muted-foreground">
          <Monitor className="w-4 h-4" /> פתיחה בתצוגה המלאה (היסטוריה, הפצה, AI)
        </Link>
      </main>

      {zoom && lesson.fileUrl && (
        <div role="dialog" aria-label="תמונה במסך מלא" className="fixed inset-0 z-50 bg-black/95 flex items-center justify-center" onClick={() => setZoom(false)}>
          <img src={lesson.fileUrl} alt="תמונה מצורפת" className="max-w-full max-h-full object-contain" />
          <button type="button" aria-label="סגור" className="absolute top-4 left-4 p-2 rounded-full bg-white/15 text-white" onClick={() => setZoom(false)}>
            <X className="w-6 h-6" />
          </button>
        </div>
      )}
    </div>
  );
};

const Item = ({ label, value, wide }: { label: string; value?: string; wide?: boolean }) =>
  value ? (
    <div className={wide ? "col-span-2" : undefined}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  ) : null;

const Section = ({ title, text }: { title: string; text?: string }) =>
  text?.trim() ? (
    <section className="space-y-1">
      <h2 className="font-semibold">{title}</h2>
      <p className="text-sm whitespace-pre-wrap leading-relaxed">{text}</p>
    </section>
  ) : null;

export default MobileLessonView;
