/**
 * The phone wizard's form (PLAN-lessons-mobile.md M1) — pure, no React.
 *
 * What a lesson needs is decided in `lessonAssignment.ts` (SAVE_REQUIRED /
 * SUBMIT_REQUIRED); this file only turns the wizard's form into a lesson and
 * back, and keeps the typed-but-not-sent copy in localStorage (M-D4).
 */
import { missingForSave, missingForSubmit, type RequiredField } from "@/lib/lessonAssignment";

export const PAST_PROJECT = "__past__";

/** The stages a lesson is filed under — the computer's dialog and the phone wizard. */
export const LESSON_STAGES = ["הקפאת תכולה", "הקפאת תצורה", "תכנון", "קבלת היתר", "בחירת קבלן", "עבודות אזרחיות", "הרכבות חשמליות", "בדיקות", "טופס 4", "חישמול"];

export interface LessonForm {
  title: string;
  description: string;
  recommendation: string;
  /** A project id, PAST_PROJECT, or "" */
  projectId: string;
  pastProjectName: string;
  stage: string;
  category: string;
  risk: "" | "high" | "medium" | "low";
  professionalDomain: string;
  /** yyyy-MM-dd */
  eventDate: string;
  equipmentIds: number[];
  impactScheduleDelay: string;
  impactBudgetCost: string;
  impactQualityDesc: string;
  assignedTo: string;
  fileUrl: string;
}

interface ProjectLike {
  id: number;
  name: string;
  managerId?: string;
}

interface UserLike {
  id: string;
  name: string;
  role: string;
  assignedProjects?: number[];
}

/** Local calendar day, yyyy-MM-dd (toISOString would give yesterday's date before 03:00 in Israel). */
export const todayIso = (now: Date = new Date()): string =>
  `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

export const emptyForm = (today: string = todayIso()): LessonForm => ({
  title: "",
  description: "",
  recommendation: "",
  projectId: "",
  pastProjectName: "",
  stage: "",
  category: "",
  risk: "",
  professionalDomain: "",
  eventDate: today,
  equipmentIds: [],
  impactScheduleDelay: "",
  impactBudgetCost: "",
  impactQualityDesc: "",
  assignedTo: "",
  fileUrl: "",
});

/**
 * Projects a user may file a lesson under — the same rule as the computer's
 * dialog: admin and referent pick any project, everyone else the ones they manage.
 * Projects the user is assigned to come first.
 */
export const selectableProjects = <P extends ProjectLike>(projects: P[], user: UserLike): P[] => {
  const all = user.role === "admin" || user.role === "referent";
  const list = all ? projects : projects.filter((p) => p.managerId === user.id);
  const mine = new Set(user.assignedProjects ?? []);
  return [...list].sort((a, b) => Number(mine.has(b.id) || b.managerId === user.id) - Number(mine.has(a.id) || a.managerId === user.id));
};

/** The project's display name, or "" when none is chosen. */
export const projectName = (form: LessonForm, projects: ProjectLike[]): string => {
  if (form.projectId === PAST_PROJECT) return form.pastProjectName.trim();
  return projects.find((p) => String(p.id) === form.projectId)?.name ?? "";
};

const asLessonFields = (form: LessonForm, projects: ProjectLike[]) => ({
  title: form.title,
  description: form.description,
  project: projectName(form, projects),
  stage: form.stage,
  category: form.category,
  risk: form.risk,
  assignedTo: form.assignedTo,
});

export const formMissingForSave = (form: LessonForm, projects: ProjectLike[]): RequiredField[] =>
  missingForSave(asLessonFields(form, projects));

export const formMissingForSubmit = (form: LessonForm, projects: ProjectLike[], users: UserLike[]): RequiredField[] =>
  missingForSubmit(asLessonFields(form, projects), users);

const toNumber = (v: string): number | undefined => {
  const t = v.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? Math.round(n) : undefined;
};

/** The wizard's form as the fields `addLesson` / `updateLesson` take. */
export const toLessonInput = (form: LessonForm, projects: ProjectLike[]) => {
  const pid = form.projectId && form.projectId !== PAST_PROJECT ? Number(form.projectId) : null;
  return {
    title: form.title.trim(),
    description: form.description.trim(),
    recommendation: form.recommendation.trim(),
    project: projectName(form, projects),
    projectId: Number.isFinite(pid) ? pid : null,
    stage: form.stage,
    category: form.category,
    risk: (form.risk || "low") as "high" | "medium" | "low",
    equipmentIds: form.equipmentIds,
    professionalDomain: form.professionalDomain || undefined,
    eventDate: form.eventDate || undefined,
    assignedTo: form.assignedTo || undefined,
    impactScheduleDelay: toNumber(form.impactScheduleDelay),
    impactBudgetCost: toNumber(form.impactBudgetCost),
    impactQualityDesc: form.impactQualityDesc.trim() || undefined,
    fileUrl: form.fileUrl || undefined,
  };
};

interface LessonLike {
  title: string;
  description: string;
  recommendation: string;
  project: string;
  projectId: number | null;
  stage: string;
  category: string;
  risk: "high" | "medium" | "low";
  equipmentIds: number[];
  professionalDomain?: string;
  eventDate?: string;
  assignedTo?: string;
  impactScheduleDelay?: number;
  impactBudgetCost?: number;
  impactQualityDesc?: string;
  fileUrl?: string;
}

/** An existing draft back into the wizard, to complete it on the phone. */
export const fromLesson = (lesson: LessonLike): LessonForm => ({
  title: lesson.title ?? "",
  description: lesson.description ?? "",
  recommendation: lesson.recommendation ?? "",
  projectId: lesson.projectId != null ? String(lesson.projectId) : lesson.project ? PAST_PROJECT : "",
  pastProjectName: lesson.projectId != null ? "" : lesson.project ?? "",
  stage: lesson.stage ?? "",
  category: lesson.category ?? "",
  risk: lesson.risk ?? "",
  professionalDomain: lesson.professionalDomain ?? "",
  eventDate: lesson.eventDate ?? "",
  equipmentIds: lesson.equipmentIds ?? [],
  impactScheduleDelay: lesson.impactScheduleDelay != null ? String(lesson.impactScheduleDelay) : "",
  impactBudgetCost: lesson.impactBudgetCost != null ? String(lesson.impactBudgetCost) : "",
  impactQualityDesc: lesson.impactQualityDesc ?? "",
  assignedTo: lesson.assignedTo ?? "",
  fileUrl: lesson.fileUrl ?? "",
});

// ---------------------------------------------------------------- the four steps

export const WIZARD_STEPS = [
  { key: "what", title: "מה קרה", fields: ["title", "description"] },
  { key: "where", title: "איפה ומתי", fields: ["project"] },
  { key: "risk", title: "סיכון וסיווג", fields: ["risk", "stage", "category"] },
  { key: "send", title: "רפרנט ושליחה", fields: ["assignedTo"] },
] as const;

/** Per step: the required fields (save or submit) still empty in it. */
export const stepGaps = (form: LessonForm, projects: ProjectLike[], users: UserLike[]): RequiredField[][] => {
  const missing = new Set<RequiredField>([
    ...formMissingForSave(form, projects),
    ...formMissingForSubmit(form, projects, users),
  ]);
  return WIZARD_STEPS.map((s) => (s.fields as readonly RequiredField[]).filter((f) => missing.has(f)));
};

export const FIELD_NAMES: Record<RequiredField, string> = {
  title: "כותרת",
  description: "מה קרה",
  project: "פרויקט",
  stage: "שלב",
  category: "קטגוריה",
  risk: "רמת סיכון",
  assignedTo: "רפרנט",
};

// ---------------------------------------------------------------- local copy (M-D4)

/** What is kept on the phone until the server has it. Never a file — only its URL once uploaded. */
export interface LocalDraft {
  form: LessonForm;
  /** The server lesson this form edits, once it exists. */
  lessonId: number | null;
  savedAt: string;
}

export const localDraftKey = (userId: string, lessonId: number | null): string =>
  `lessons.wizard.${userId}.${lessonId ?? "new"}`;

export const saveLocalDraft = (userId: string, draft: LocalDraft): void => {
  try {
    localStorage.setItem(localDraftKey(userId, draft.lessonId), JSON.stringify(draft));
  } catch {
    /* private mode / full storage — the wizard still works, only without the safety copy */
  }
};

export const loadLocalDraft = (userId: string, lessonId: number | null): LocalDraft | null => {
  try {
    const raw = localStorage.getItem(localDraftKey(userId, lessonId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LocalDraft;
    if (!parsed || typeof parsed !== "object" || !parsed.form) return null;
    // Fields added after the copy was written get their empty value.
    return { ...parsed, form: { ...emptyForm(""), ...parsed.form } };
  } catch {
    return null;
  }
};

export const clearLocalDraft = (userId: string, lessonId: number | null): void => {
  try {
    localStorage.removeItem(localDraftKey(userId, lessonId));
  } catch {
    /* nothing to clear */
  }
};

/** True when the form holds anything the user typed — an untouched form is not worth keeping. */
export const hasContent = (form: LessonForm): boolean =>
  [form.title, form.description, form.recommendation, form.pastProjectName, form.impactQualityDesc]
    .some((v) => v.trim() !== "") || form.projectId !== "" || form.risk !== "";
