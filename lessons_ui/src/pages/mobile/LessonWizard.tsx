import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Camera, Check, ChevronLeft, ChevronRight, Loader2, Paperclip, Search, X } from "lucide-react";
import { useUser, type Lesson } from "@/context/UserContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import ChoiceChips from "@/components/mobile/ChoiceChips";
import { useFieldLabels } from "@/hooks/useFieldLabels";
import { PROFESSIONAL_DOMAINS, riskLabelsShort } from "@/lib/constants";
import { isDraft, referentOptions, type RequiredField } from "@/lib/lessonAssignment";
import {
  FIELD_NAMES,
  LESSON_STAGES,
  PAST_PROJECT,
  WIZARD_STEPS,
  clearLocalDraft,
  emptyForm,
  formMissingForSave,
  formMissingForSubmit,
  fromLesson,
  hasContent,
  loadLocalDraft,
  saveLocalDraft,
  selectableProjects,
  stepGaps,
  toLessonInput,
  type LessonForm,
} from "@/lib/lessonDraft";
import { uploadLessonFile } from "@/lib/lessonFiles";
import { cn } from "@/lib/utils";

// The phone wizard (PLAN-lessons-mobile.md M1): four short steps, the minimum to
// save a draft, the whole lesson completable here. No AI (owner decision M-D2).

const RISK_STYLES: Record<string, string> = {
  high: "border-destructive text-destructive data-[on=true]:bg-destructive data-[on=true]:text-destructive-foreground",
  medium: "border-warning text-warning data-[on=true]:bg-warning data-[on=true]:text-warning-foreground",
  low: "border-success text-success data-[on=true]:bg-success data-[on=true]:text-success-foreground",
};
const MAX_PROJECTS_LISTED = 40;

type Done = null | "draft" | "submitted";

const LessonWizard = () => {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { currentUser, users, projects, equipment, lessons, lessonCategories, addLesson, updateLesson, submitDraftLesson } = useUser();
  const { getLabel } = useFieldLabels("lesson");

  const editId = id ? Number(id) : null;
  const editing = editId != null ? lessons.find((l) => l.id === editId) ?? null : null;
  const editAllowed = editing != null && isDraft(editing) && editing.createdBy === currentUser.id;

  const [form, setForm] = useState<LessonForm>(() => emptyForm());
  const [lessonId, setLessonId] = useState<number | null>(editId);
  const [ready, setReady] = useState(false);
  const [resumed, setResumed] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [showErrors, setShowErrors] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Done>(null);
  const [projectQuery, setProjectQuery] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const createdRef = useRef<Lesson | null>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Load once: the phone's unsent copy wins over the server row (it is newer by definition).
  useEffect(() => {
    if (ready) return;
    if (editId != null && !editing) return; // not found — the page says so
    const local = loadLocalDraft(currentUser.id, editId);
    if (local && hasContent(local.form)) {
      setForm(local.form);
      setResumed(local.savedAt);
    } else if (editing) {
      setForm(fromLesson(editing));
    } else {
      const project = searchParams.get("project");
      if (project) setForm((f) => ({ ...f, projectId: project }));
    }
    setReady(true);
  }, [ready, editId, editing, currentUser.id, searchParams]);

  // M-D4: every keystroke is kept on the phone until the server has it.
  useEffect(() => {
    if (!ready || done) return;
    if (hasContent(form)) saveLocalDraft(currentUser.id, { form, lessonId, savedAt: new Date().toISOString() });
  }, [form, ready, done, lessonId, currentUser.id]);

  useEffect(() => {
    if (!file || !file.type.startsWith("image/")) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const projectList = useMemo(() => selectableProjects(projects, currentUser), [projects, currentUser]);
  const shownProjects = useMemo(() => {
    const q = projectQuery.trim();
    const list = q ? projectList.filter((p) => p.name.includes(q)) : projectList;
    return list.slice(0, MAX_PROJECTS_LISTED);
  }, [projectList, projectQuery]);
  const referents = useMemo(() => referentOptions(users, editing?.assignedTo), [users, editing?.assignedTo]);
  const gaps = stepGaps(form, projects, users);
  const missingSave = formMissingForSave(form, projects);
  const missingSubmit = formMissingForSubmit(form, projects, users);

  const set = <K extends keyof LessonForm>(key: K, value: LessonForm[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    setError(null);
  };
  const toggle = <K extends "stage" | "category" | "professionalDomain" | "risk" | "assignedTo">(key: K, value: string) =>
    set(key, (form[key] === value ? "" : value) as LessonForm[K]);
  const toggleEquipment = (eqId: number) =>
    set("equipmentIds", form.equipmentIds.includes(eqId) ? form.equipmentIds.filter((x) => x !== eqId) : [...form.equipmentIds, eqId]);

  const fieldError = (f: RequiredField, forSubmitOnly = false) =>
    showErrors && (forSubmitOnly ? missingSubmit : [...missingSave, ...missingSubmit]).includes(f) ? (
      <p className="text-xs text-destructive mt-1">{forSubmitOnly ? "נדרש לשליחה לאישור" : "חובה"}</p>
    ) : null;

  const persist = async (mode: "draft" | "submit") => {
    const missing = mode === "draft" ? missingSave : missingSubmit;
    if (missing.length > 0) {
      setShowErrors(true);
      const first = WIZARD_STEPS.findIndex((s) => (s.fields as readonly RequiredField[]).some((f) => missing.includes(f)));
      if (first >= 0) setStep(first);
      setError(`חסר: ${missing.map((f) => FIELD_NAMES[f]).join(", ")}`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      let fileUrl = form.fileUrl;
      if (file) {
        fileUrl = await uploadLessonFile(file);
        setForm((f) => ({ ...f, fileUrl }));
        setFile(null);
      }
      const input = toLessonInput({ ...form, fileUrl }, projects);
      if (lessonId == null) {
        // A new lesson with a referent goes straight to approval; "draft" keeps it with its creator.
        const created = await addLesson(input, { draft: mode === "draft" });
        createdRef.current = created;
        clearLocalDraft(currentUser.id, null);
        setLessonId(created.id);
      } else {
        const updates = {
          ...input,
          // "" clears a field the user emptied (undefined would leave the old value).
          professionalDomain: input.professionalDomain ?? "",
          eventDate: input.eventDate ?? "",
          assignedTo: input.assignedTo ?? "",
          impactQualityDesc: input.impactQualityDesc ?? "",
          fileUrl: input.fileUrl ?? "",
        };
        await updateLesson(lessonId, updates);
        clearLocalDraft(currentUser.id, lessonId);
        if (mode === "submit") {
          const base = lessons.find((l) => l.id === lessonId) ?? createdRef.current;
          if (!base) throw new Error("lesson not loaded");
          await submitDraftLesson(lessonId, { ...base, ...updates });
        }
      }
      setDone(mode === "draft" ? "draft" : "submitted");
    } catch (e) {
      console.error("Wizard save error:", e);
      setError("לא נשלח — הטקסט שמור בטלפון. בדוק קליטה ונסה שוב.");
    } finally {
      setSaving(false);
    }
  };

  const startNew = () => {
    clearLocalDraft(currentUser.id, null);
    createdRef.current = null;
    setForm(emptyForm());
    setLessonId(null);
    setStep(0);
    setShowErrors(false);
    setFile(null);
    setResumed(null);
    setError(null);
    setDone(null);
    navigate("/m/new", { replace: true });
  };

  const exit = () => navigate("/lessons");

  // First: a lesson just sent is no longer a draft, and must not read as "not allowed".
  if (done) {
    return (
      <div className="min-h-[100dvh] flex flex-col items-center justify-center gap-6 p-6 text-center">
        <div className="w-16 h-16 rounded-full bg-success/15 flex items-center justify-center">
          <Check className="w-8 h-8 text-success" />
        </div>
        <div className="space-y-2">
          <h1 className="text-xl font-bold">{done === "submitted" ? "הלקח נשלח לאישור" : "נשמר כטיוטה"}</h1>
          <p className="text-muted-foreground">
            {done === "submitted"
              ? "מנהל המערכת יקבל הודעה. אחרי האישור הלקח יעבור לרפרנט שבחרת."
              : missingSubmit.length > 0
                ? `רק אתה רואה אותו. כדי לשלוח לאישור חסר: ${missingSubmit.map((f) => FIELD_NAMES[f]).join(", ")}.`
                : "רק אתה רואה אותו. אפשר לשלוח לאישור מתי שתרצה."}
          </p>
        </div>
        <div className="w-full max-w-sm space-y-3">
          {done === "draft" && (
            <Button className="w-full h-12 text-base" onClick={() => { setDone(null); setStep(0); navigate(`/m/lessons/${lessonId}/edit`, { replace: true }); }}>
              המשך להשלים
            </Button>
          )}
          <Button variant={done === "draft" ? "outline" : "default"} className="w-full h-12 text-base" onClick={startNew}>
            ➕ לקח חדש
          </Button>
          <Button variant="outline" className="w-full h-12 text-base" onClick={() => navigate("/m/drafts")}>
            הטיוטות שלי
          </Button>
          <Button variant="ghost" className="w-full h-12 text-base" onClick={exit}>
            לרשימת הלקחים
          </Button>
        </div>
      </div>
    );
  }

  // MobileShell renders only after the provider loaded every lesson.
  if (editId != null && !editing) {
    return <Message text="הלקח לא נמצא." />;
  }
  if (editId != null && editing && !editAllowed) {
    return <Message text="אפשר להשלים כאן רק טיוטה שאתה יצרת. לקח שכבר נשלח לאישור נערך מהמחשב." />;
  }
  if (!ready) {
    return (
      <div className="min-h-[100dvh] flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const last = step === WIZARD_STEPS.length - 1;

  return (
    <div className="min-h-[100dvh] flex flex-col">
      {/* Header: exit, title, progress (tap a step to jump — completing a draft is not linear) */}
      <header className="sticky top-0 z-20 bg-background/95 backdrop-blur border-b border-border px-4 pt-3 pb-2 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Button variant="ghost" size="icon" onClick={exit} aria-label="יציאה">
            <X className="w-5 h-5" />
          </Button>
          <div className="text-center min-w-0">
            <p className="text-xs text-muted-foreground">
              {lessonId != null ? "השלמת טיוטה" : "לקח חדש"} · שלב {step + 1} מתוך {WIZARD_STEPS.length}
            </p>
            <h1 className="font-heading font-bold text-lg truncate">{WIZARD_STEPS[step].title}</h1>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="text-primary"
            disabled={saving || missingSave.length > 0}
            onClick={() => persist("draft")}
          >
            שמור
          </Button>
        </div>
        <nav className="grid grid-cols-4 gap-1.5" aria-label="שלבים">
          {WIZARD_STEPS.map((s, i) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setStep(i)}
              aria-current={i === step ? "step" : undefined}
              aria-label={`${s.title}${gaps[i].length ? " — חסרים שדות" : ""}`}
              className="py-2"
            >
              <span
                className={cn(
                  "block h-1.5 rounded-full",
                  i === step ? "bg-primary" : gaps[i].length === 0 ? "bg-primary/40" : "bg-muted",
                  showErrors && gaps[i].length > 0 && "bg-destructive/60",
                )}
              />
            </button>
          ))}
        </nav>
      </header>

      {resumed && (
        <div className="mx-4 mt-3 rounded-lg bg-info/10 text-sm px-3 py-2 flex items-center justify-between gap-2">
          <span>המשכנו מאיפה שעצרת (נשמר בטלפון).</span>
          {lessonId == null && (
            <button type="button" className="text-primary underline shrink-0" onClick={startNew}>התחל מחדש</button>
          )}
        </div>
      )}

      <main className="flex-1 px-4 py-4 space-y-6 pb-32">
        {step === 0 && (
          <>
            <Field label={getLabel("title", "כותרת הלקח")} required error={fieldError("title")}>
              <Input
                value={form.title}
                onChange={(e) => set("title", e.target.value)}
                placeholder="במשפט אחד — מה הלקח?"
                enterKeyHint="next"
                className="h-12 text-base"
              />
            </Field>
            <Field label={getLabel("description", "מה קרה")} required error={fieldError("description")}
              hint="אפשר להכתיב: לחצו על המיקרופון במקלדת.">
              <Textarea
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder={"מה קרה בפועל?\nמה גרם לזה?\nאיך זה השפיע על הפרויקט?"}
                rows={6}
                className="text-base"
              />
            </Field>
            <Field label={getLabel("recommendation", "המלצה")} hint="אופציונלי — מה לעשות אחרת בפעם הבאה.">
              <Textarea value={form.recommendation} onChange={(e) => set("recommendation", e.target.value)} rows={3} className="text-base" />
            </Field>
          </>
        )}

        {step === 1 && (
          <>
            <Field label={getLabel("project_name", "פרויקט")} required error={fieldError("project")}>
              <div className="relative">
                <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  value={projectQuery}
                  onChange={(e) => setProjectQuery(e.target.value)}
                  placeholder="חיפוש פרויקט..."
                  enterKeyHint="search"
                  className="h-12 pr-9 text-base"
                />
              </div>
              <ul className="mt-2 space-y-1.5" role="radiogroup" aria-label="פרויקט">
                {shownProjects.map((p) => (
                  <li key={p.id}>
                    <RowChoice selected={form.projectId === String(p.id)} onClick={() => set("projectId", String(p.id))}>
                      {p.name}
                    </RowChoice>
                  </li>
                ))}
                {shownProjects.length === 0 && projectList.length > 0 && (
                  <li className="text-sm text-muted-foreground px-1">לא נמצא פרויקט בשם הזה.</li>
                )}
                <li>
                  <RowChoice selected={form.projectId === PAST_PROJECT} onClick={() => set("projectId", PAST_PROJECT)}>
                    📁 פרויקט היסטורי / אחר
                  </RowChoice>
                </li>
              </ul>
              {form.projectId === PAST_PROJECT && (
                <Input
                  value={form.pastProjectName}
                  onChange={(e) => set("pastProjectName", e.target.value)}
                  placeholder="שם הפרויקט"
                  className="h-12 text-base mt-2"
                />
              )}
            </Field>
            <Field label={getLabel("event_date", "תאריך האירוע")}>
              <Input type="date" value={form.eventDate} onChange={(e) => set("eventDate", e.target.value)} className="h-12 text-base" />
            </Field>
          </>
        )}

        {step === 2 && (
          <>
            <Field label={getLabel("risk", "רמת סיכון")} required error={fieldError("risk")}>
              <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="רמת סיכון">
                {(["high", "medium", "low"] as const).map((r) => (
                  <button
                    key={r}
                    type="button"
                    role="radio"
                    aria-checked={form.risk === r}
                    data-on={form.risk === r}
                    onClick={() => toggle("risk", r)}
                    className={cn("h-14 rounded-xl border-2 font-semibold text-base", RISK_STYLES[r])}
                  >
                    {riskLabelsShort[r]}
                  </button>
                ))}
              </div>
            </Field>
            <Field label={getLabel("stage", "שלב")} submitOnly error={fieldError("stage", true)}>
              <ChoiceChips label="שלב" options={LESSON_STAGES.map((s) => ({ value: s, label: s }))} value={form.stage} onChange={(v) => toggle("stage", v)} />
            </Field>
            <Field label={getLabel("category", "קטגוריה")} submitOnly error={fieldError("category", true)}>
              <ChoiceChips label="קטגוריה" options={lessonCategories.map((c) => ({ value: c, label: c }))} value={form.category} onChange={(v) => toggle("category", v)} />
            </Field>
            <Field label={getLabel("professional_domain", "תחום מקצועי / מגזר")}>
              <ChoiceChips label="תחום" options={PROFESSIONAL_DOMAINS.map((d) => ({ value: d, label: d }))} value={form.professionalDomain} onChange={(v) => toggle("professionalDomain", v)} />
            </Field>
            <div className="rounded-xl border border-border">
              <button type="button" className="w-full flex items-center justify-between px-4 py-3 min-h-12 text-sm font-medium"
                aria-expanded={moreOpen} onClick={() => setMoreOpen((o) => !o)}>
                <span>ציוד והשפעה (אופציונלי)</span>
                {moreOpen ? <ChevronLeft className="w-4 h-4 -rotate-90" /> : <ChevronLeft className="w-4 h-4" />}
              </button>
              {moreOpen && (
                <div className="px-4 pb-4 space-y-5">
                  {equipment.length > 0 && (
                    <Field label={getLabel("equipment_ids", "ציוד / נושאים")}>
                      <ChoiceChips multiple label="ציוד" options={equipment.map((e) => ({ value: e.id, label: e.name }))} value={form.equipmentIds} onChange={toggleEquipment} />
                    </Field>
                  )}
                  <Field label={getLabel("impact_schedule_delay", "עיכוב בלו״ז (ימים)")}>
                    <Input inputMode="numeric" value={form.impactScheduleDelay} onChange={(e) => set("impactScheduleDelay", e.target.value.replace(/[^\d]/g, ""))} className="h-12 text-base" />
                  </Field>
                  <Field label={getLabel("impact_budget_cost", "עלות (₪)")}>
                    <Input inputMode="numeric" value={form.impactBudgetCost} onChange={(e) => set("impactBudgetCost", e.target.value.replace(/[^\d]/g, ""))} className="h-12 text-base" />
                  </Field>
                  <Field label={getLabel("impact_quality_desc", "השפעה על איכות")}>
                    <Textarea value={form.impactQualityDesc} onChange={(e) => set("impactQualityDesc", e.target.value)} rows={2} className="text-base" />
                  </Field>
                </div>
              )}
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <Field label={getLabel("file_url", "תמונה / קובץ")} hint="תמונות מוקטנות בטלפון לפני השליחה.">
              <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden"
                onChange={(e) => { setFile(e.target.files?.[0] ?? null); e.target.value = ""; }} />
              <input ref={fileRef} type="file" accept="image/*,.pdf,.doc,.docx,.xls,.xlsx" className="hidden"
                onChange={(e) => { setFile(e.target.files?.[0] ?? null); e.target.value = ""; }} />
              <div className="grid grid-cols-2 gap-2">
                <Button type="button" variant="outline" className="h-12 gap-2" onClick={() => cameraRef.current?.click()}>
                  <Camera className="w-5 h-5" /> צלם
                </Button>
                <Button type="button" variant="outline" className="h-12 gap-2" onClick={() => fileRef.current?.click()}>
                  <Paperclip className="w-5 h-5" /> קובץ
                </Button>
              </div>
              {(file || form.fileUrl) && (
                <div className="mt-2 flex items-center gap-3 rounded-lg border border-border p-2">
                  {preview ? <img src={preview} alt="" className="w-14 h-14 object-cover rounded" /> : <Paperclip className="w-5 h-5 text-muted-foreground" />}
                  <span className="flex-1 min-w-0 truncate text-sm">{file ? file.name : "קובץ מצורף ✓"}</span>
                  <Button type="button" variant="ghost" size="icon" aria-label="הסר קובץ"
                    onClick={() => { setFile(null); set("fileUrl", ""); }}>
                    <X className="w-4 h-4" />
                  </Button>
                </div>
              )}
            </Field>
            <Field label={getLabel("assigned_to", "רפרנט (אחראי ליישום)")} submitOnly error={fieldError("assignedTo", true)}
              hint="בלי רפרנט הלקח נשמר כטיוטה, ורק אתה רואה אותו.">
              <ul className="space-y-1.5" role="radiogroup" aria-label="רפרנט">
                {referents.map((o) => (
                  <li key={o.value}>
                    <RowChoice selected={form.assignedTo === o.value} onClick={() => toggle("assignedTo", o.value)}>{o.label}</RowChoice>
                  </li>
                ))}
                {referents.length === 0 && <li className="text-sm text-muted-foreground">אין רפרנטים מוגדרים במערכת.</li>}
              </ul>
            </Field>
            <section className="rounded-xl bg-muted/50 p-4 space-y-1 text-sm" aria-live="polite">
              {missingSubmit.length === 0 ? (
                <p className="font-medium text-success">✓ הכול מוכן לשליחה לאישור</p>
              ) : (
                <>
                  <p className="font-medium">כדי לשלוח לאישור חסר:</p>
                  <p className="text-muted-foreground">{missingSubmit.map((f) => FIELD_NAMES[f]).join(" · ")}</p>
                  <p className="text-muted-foreground">אפשר לשמור עכשיו כטיוטה ולהשלים אחר כך.</p>
                </>
              )}
            </section>
          </>
        )}
      </main>

      {/* Bottom bar — sticks above the keyboard on Android; the main area pads for it */}
      <footer className="fixed bottom-0 inset-x-0 z-20 bg-background/95 backdrop-blur border-t border-border px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] space-y-2">
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {!last ? (
          <div className="flex gap-2">
            {step > 0 && (
              <Button variant="outline" className="h-12 flex-1 gap-1" onClick={() => setStep(step - 1)}>
                <ChevronRight className="w-4 h-4" /> הקודם
              </Button>
            )}
            <Button className="h-12 flex-[2] gap-1 text-base" onClick={() => { setStep(step + 1); window.scrollTo(0, 0); }}>
              הבא <ChevronLeft className="w-4 h-4" />
            </Button>
          </div>
        ) : (
          <div className="flex gap-2">
            <Button variant="outline" className="h-12 flex-1 text-base" disabled={saving} onClick={() => persist("draft")}>
              שמור טיוטה
            </Button>
            <Button className="h-12 flex-1 text-base" disabled={saving} onClick={() => persist("submit")}>
              {saving ? <Loader2 className="w-5 h-5 animate-spin" /> : "שלח לאישור"}
            </Button>
          </div>
        )}
      </footer>
    </div>
  );
};

const Field = ({ label, required, submitOnly, hint, error, children }: {
  label: string;
  required?: boolean;
  submitOnly?: boolean;
  hint?: string;
  error?: React.ReactNode;
  children: React.ReactNode;
}) => (
  <div className="space-y-2">
    <p className="text-sm font-medium">
      {label}
      {required && <span className="text-destructive"> *</span>}
      {submitOnly && <span className="text-xs font-normal text-muted-foreground"> (נדרש לשליחה)</span>}
    </p>
    {children}
    {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    {error}
  </div>
);

const RowChoice = ({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: React.ReactNode }) => (
  <button
    type="button"
    role="radio"
    aria-checked={selected}
    onClick={onClick}
    className={cn(
      "w-full text-right rounded-lg px-4 py-3 min-h-12 border flex items-center justify-between gap-2",
      selected ? "border-primary bg-primary/10 font-medium" : "border-border active:bg-muted",
    )}
  >
    <span className="min-w-0">{children}</span>
    {selected && <Check className="w-4 h-4 text-primary shrink-0" />}
  </button>
);

const Message = ({ text }: { text: string }) => (
  <div className="min-h-[100dvh] flex flex-col items-center justify-center gap-4 p-6 text-center">
    <p className="text-lg">{text}</p>
    <Link to="/m/drafts" className="text-primary underline">הטיוטות שלי</Link>
  </div>
);

export default LessonWizard;
