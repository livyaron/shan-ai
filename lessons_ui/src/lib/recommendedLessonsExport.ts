import jsPDF from "jspdf";
import html2canvas from "html2canvas";
import {
  Document,
  Packer,
  Paragraph,
  TextRun,
  HeadingLevel,
  AlignmentType,
  PageBreak,
  BorderStyle,
  Table,
  TableRow,
  TableCell,
  WidthType,
  ShadingType,
  VerticalAlign,
} from "docx";
import { buildSummary } from "./presentationExport";

/**
 * Recommended Lessons document — auto-selection + DOCX/PDF export.
 * Selection is automatic; the preview UI may only DESELECT lessons.
 */

export interface RecLesson {
  id: number;
  title: string;
  description: string;
  recommendation?: string;
  stage: string;
  category?: string;
  professionalDomain?: string;
  risk: "high" | "medium" | "low";
  equipmentIds: number[];
  status?: string;
  workflowStatus: string;
  projectId: number | null;
  eventDate?: string;
  date?: string;
  sourceProjectType?: string;
  sourceStationType?: string;
}

export interface RecProject {
  name: string;
  projectType: string;
  stationType: string;
  site?: string;
  equipmentIds: number[];
  /** Ordered stage names active in this project (chronological). */
  stages: string[];
}

export interface QualityInfo {
  shrunk: number;
  avgScore: number | null;
  implementationRate: number | null;
  responses: number;
}

export const ELIGIBLE_WORKFLOW_STATUSES = ["אושר והופץ", "נסגר"] as const;
export const ELIGIBLE_LESSON_STATUSES = ["approved", "distributed", "closed"] as const;

export const SCORE_WEIGHTS = {
  stage: 28,
  adjacentStage: 14,
  equipmentOverlap: 18,
  projectType: 12,
  stationType: 12,
  category: 8,
  professionalDomain: 8,
  risk: 3,
  // Quality is a SECONDARY tiebreaker — never a hard threshold and never
  // applied to lessons without responses (those are treated as neutral).
  quality: 6,
  implementationRate: 3,
};

export const MAX_PER_STAGE = 10;

export interface ScoredLesson extends RecLesson {
  /** Selection layer: 1 strong, 2 partial/contextual, 3 general fallback. */
  selectionLayer: 1 | 2 | 3;
  /** Match-only score (project fit). Primary sort key. */
  matchScore: number;
  /** Quality boost — 0 when no responses yet (neutral, not penalized). */
  qualityBoost: number;
  /** matchScore + qualityBoost — for display. */
  finalScore: number;
  quality?: QualityInfo;
}

const norm = (s?: string) => (s || "").trim();

const PROJECT_STAGE_ORDER = [
  "הקפאת תכולה",
  "הקפאת תצורה",
  "תכנון",
  "קבלת היתר",
  "בחירת קבלן",
  "עבודות אזרחיות",
  "הרכבות חשמליות",
  "בדיקות",
  "טופס 4",
  "הסתיים",
  "חישמול",
];

const stageIndex = (stage?: string) => PROJECT_STAGE_ORDER.findIndex((s) => norm(s) === norm(stage));
const isAdjacentStage = (lessonStage: string | undefined, targetStage: string) => {
  const a = stageIndex(lessonStage);
  const b = stageIndex(targetStage);
  return a >= 0 && b >= 0 && Math.abs(a - b) === 1;
};

const hasEquipmentOverlap = (lesson: RecLesson, project: RecProject) =>
  project.equipmentIds.length > 0 && lesson.equipmentIds.some((id) => project.equipmentIds.includes(id));

const isEligibleLesson = (lesson: RecLesson) => {
  const workflow = norm(lesson.workflowStatus);
  const status = norm(lesson.status);
  if (workflow === "נדחה" || status === "rejected") return false;
  return (
    ELIGIBLE_WORKFLOW_STATUSES.includes(workflow as (typeof ELIGIBLE_WORKFLOW_STATUSES)[number]) ||
    ELIGIBLE_LESSON_STATUSES.includes(status as (typeof ELIGIBLE_LESSON_STATUSES)[number])
  );
};

/** Project-fit score only — no quality involvement. */
export function scoreMatch(
  lesson: RecLesson,
  project: RecProject,
  projectCategories: Set<string>,
  projectDomains: Set<string>,
  targetStage?: string,
): number {
  let score = 0;
  const stagesForScore = targetStage ? [targetStage] : project.stages;
  if (lesson.stage && stagesForScore.some((s) => norm(s) === norm(lesson.stage)))
    score += SCORE_WEIGHTS.stage;
  else if (targetStage && isAdjacentStage(lesson.stage, targetStage))
    score += SCORE_WEIGHTS.adjacentStage;

  if (project.equipmentIds.length > 0 && lesson.equipmentIds.length > 0) {
    const overlap = lesson.equipmentIds.filter((id) => project.equipmentIds.includes(id)).length;
    score += (overlap / project.equipmentIds.length) * SCORE_WEIGHTS.equipmentOverlap;
  }

  if (lesson.sourceProjectType && lesson.sourceProjectType === project.projectType)
    score += SCORE_WEIGHTS.projectType;
  if (lesson.sourceStationType && lesson.sourceStationType === project.stationType)
    score += SCORE_WEIGHTS.stationType;

  if (lesson.category && projectCategories.has(lesson.category)) score += SCORE_WEIGHTS.category;
  if (lesson.professionalDomain && projectDomains.has(lesson.professionalDomain))
    score += SCORE_WEIGHTS.professionalDomain;

  score += lesson.risk === "high" ? SCORE_WEIGHTS.risk : lesson.risk === "medium" ? 2 : 1;
  return Math.round(score * 10) / 10;
}

/** Quality boost — returns 0 for lessons with no responses yet. */
export function qualityBoostFor(quality: QualityInfo | undefined): number {
  if (!quality || !quality.responses || quality.responses <= 0) return 0;
  const fromAvg = ((quality.avgScore ?? 1) / 2) * SCORE_WEIGHTS.quality;
  const fromRate = (quality.implementationRate ?? 0) * SCORE_WEIGHTS.implementationRate;
  return Math.round((fromAvg + fromRate) * 10) / 10;
}

/** Back-compat: combined score (match + quality). */
export function scoreLesson(
  lesson: RecLesson,
  project: RecProject,
  quality: QualityInfo | undefined,
  projectCategories: Set<string>,
  projectDomains: Set<string>,
): number {
  return scoreMatch(lesson, project, projectCategories, projectDomains) + qualityBoostFor(quality);
}

const ts = (s?: string) => {
  if (!s) return -Infinity;
  const t = new Date(s).getTime();
  return Number.isFinite(t) ? t : -Infinity;
};

/**
 * Layered selection — per selected project stage:
 *   Layer 1: exact stage matches, sorted by project fit first and quality boost second.
 *   Layer 2: controlled expansion to adjacent stages or partial contextual matches.
 *   Layer 3: general eligible lessons, prioritizing similar project type/station when available.
 * Quality is never a gate: lessons without responses receive no boost and no penalty.
 */
export function selectRecommendedLessons(
  project: RecProject,
  allLessons: RecLesson[],
  qualityById: Map<number, QualityInfo>,
  currentProjectId: number | null,
): { stageName: string; lessons: ScoredLesson[] }[] {
  const projectCategories = new Set<string>();
  const projectDomains = new Set<string>();
  for (const l of allLessons) {
    if (l.projectId === currentProjectId) {
      if (l.category) projectCategories.add(l.category);
      if (l.professionalDomain) projectDomains.add(l.professionalDomain);
    }
  }

  const eligible = allLessons.filter((l) => isEligibleLesson(l) && l.projectId !== currentProjectId);

  const hasContextualMatch = (l: RecLesson, stage: string) => {
    if (isAdjacentStage(l.stage, stage)) return true;
    if (hasEquipmentOverlap(l, project)) return true;
    if (l.sourceProjectType && l.sourceProjectType === project.projectType) return true;
    if (l.sourceStationType && l.sourceStationType === project.stationType) return true;
    if (l.category && projectCategories.has(l.category)) return true;
    if (l.professionalDomain && projectDomains.has(l.professionalDomain)) return true;
    return false;
  };

  const hasGeneralProjectRelevance = (l: RecLesson) => {
    if (l.sourceProjectType && l.sourceProjectType === project.projectType) return true;
    if (l.sourceStationType && l.sourceStationType === project.stationType) return true;
    if (hasEquipmentOverlap(l, project)) return true;
    if (l.category && projectCategories.has(l.category)) return true;
    if (l.professionalDomain && projectDomains.has(l.professionalDomain)) return true;
    return false;
  };

  const toScored = (l: RecLesson, targetStage: string, selectionLayer: 1 | 2 | 3): ScoredLesson => {
    const q = qualityById.get(l.id);
    const matchScore = scoreMatch(l, project, projectCategories, projectDomains, targetStage);
    const qualityBoost = qualityBoostFor(q);
    return {
      ...l,
      selectionLayer,
      quality: q,
      matchScore,
      qualityBoost,
      finalScore: Math.round((matchScore + qualityBoost) * 10) / 10,
    };
  };

  // Primary key: matchScore (project fit). Quality is only a tiebreaker, so
  // lessons without quality scores are NOT penalized vs lessons with them.
  const sortFn = (a: ScoredLesson, b: ScoredLesson) => {
    if (a.selectionLayer !== b.selectionLayer) return a.selectionLayer - b.selectionLayer;
    if (b.matchScore !== a.matchScore) return b.matchScore - a.matchScore;
    if (b.qualityBoost !== a.qualityBoost) return b.qualityBoost - a.qualityBoost;
    const ea = ts(a.eventDate), eb = ts(b.eventDate);
    if (ea !== eb) return eb - ea;
    const da = ts(a.date), db = ts(b.date);
    if (da !== db) return db - da;
    return b.id - a.id;
  };

  const stageNames = project.stages.length > 0 ? project.stages : Array.from(new Set(eligible.map((l) => l.stage).filter(Boolean)));

  const result: { stageName: string; lessons: ScoredLesson[] }[] = [];
  for (const stage of stageNames) {
    const picked = new Map<number, ScoredLesson>();
    const addLayer = (items: ScoredLesson[]) => {
      for (const l of items.sort(sortFn)) {
        if (picked.size >= MAX_PER_STAGE) break;
        if (!picked.has(l.id)) picked.set(l.id, l);
      }
    };

    // Layer 1 — strong/exact stage fit
    const layer1 = eligible
      .filter((l) => norm(l.stage) === norm(stage))
      .map((l) => toScored(l, stage, 1))
      .sort(sortFn);
    addLayer(layer1);

    // Layer 2 — adjacent stages / partial contextual match
    if (picked.size < MAX_PER_STAGE) {
      const layer2 = eligible
        .filter((l) => !picked.has(l.id) && norm(l.stage) !== norm(stage) && hasContextualMatch(l, stage))
        .map((l) => toScored(l, stage, 2))
        .sort(sortFn);
      addLayer(layer2);
    }

    // Layer 3 — general fill from eligible lessons, preferring broad project relevance.
    if (picked.size < MAX_PER_STAGE) {
      const remaining = eligible.filter((l) => !picked.has(l.id));
      const relevant = remaining.filter(hasGeneralProjectRelevance);
      addLayer(relevant.map((l) => toScored(l, stage, 3)).sort(sortFn));
      if (picked.size < MAX_PER_STAGE) {
        addLayer(remaining.filter((l) => !picked.has(l.id)).map((l) => toScored(l, stage, 3)).sort(sortFn));
      }
    }

    result.push({ stageName: stage, lessons: Array.from(picked.values()).sort(sortFn) });
  }
  return result;
}

// ============== Document model ==============

export interface DocPayload {
  project: RecProject;
  generatedAt: string;
  stages: { stageName: string; lessons: ScoredLesson[] }[];
  totalSelected: number;
}

export function buildSummarySection(payload: DocPayload) {
  const all = payload.stages.flatMap((s) => s.lessons);
  const top5 = [...all].sort((a, b) => b.finalScore - a.finalScore).slice(0, 5);

  const catCount = new Map<string, number>();
  for (const l of all) {
    const k = l.category || l.professionalDomain;
    if (!k) continue;
    catCount.set(k, (catCount.get(k) || 0) + 1);
  }
  const recurring = Array.from(catCount.entries())
    .filter(([, n]) => n >= 2)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${k} (${n})`);

  const highQuality = all.filter((l) => (l.quality?.shrunk ?? 0) >= 1.5);

  return { top5, recurring, highQuality };
}

const formatPct = (r: number | null | undefined) =>
  r === null || r === undefined ? "—" : `${Math.round(r * 100)}%`;
const formatScore = (s: number | null | undefined) =>
  s === null || s === undefined ? "—" : `${s.toFixed(2)}/2`;

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// ============== PDF Export (html2canvas — perfect Hebrew/RTL) ==============

function buildPdfHtml(payload: DocPayload): string {
  const { top5, recurring, highQuality } = buildSummarySection(payload);

  const stageHtml = payload.stages
    .map((stage) => {
      const lessons = stage.lessons.length === 0
        ? `<div style="color:#888;padding:8px 0;">אין לקחים מומלצים לשלב זה כרגע</div>`
        : stage.lessons
            .map((l, i) => {
              const meta: string[] = [];
              if (l.category) meta.push(escapeHtml(l.category));
              if (l.professionalDomain) meta.push(escapeHtml(l.professionalDomain));
              meta.push(`ניקוד איכות: ${formatScore(l.quality?.shrunk)}`);
              meta.push(`אחוז יישום: ${formatPct(l.quality?.implementationRate)}`);
              meta.push(`התאמה: ${l.finalScore.toFixed(0)}`);
              return `
                <div style="border:1px solid #e5e7eb;border-radius:8px;padding:12px;margin-bottom:10px;background:#fff;page-break-inside:avoid;">
                  <div style="color:#1E2761;font-weight:700;font-size:15px;margin-bottom:6px;">${i + 1}. ${escapeHtml(l.title)}</div>
                  <div style="font-size:12px;color:#212121;line-height:1.6;white-space:pre-wrap;">${escapeHtml(buildSummary(l.description))}</div>
                  ${l.recommendation ? `<div style="margin-top:6px;font-size:12px;"><span style="color:#1E2761;font-weight:700;">המלצה: </span>${escapeHtml(buildSummary(l.recommendation))}</div>` : ""}
                  <div style="margin-top:6px;font-size:10.5px;color:#666;">${meta.join(" · ")}</div>
                </div>`;
            })
            .join("");
      return `
        <section style="page-break-inside:avoid;margin-bottom:18px;">
          <h2 style="background:#1E2761;color:#fff;padding:8px 12px;border-radius:6px;font-size:16px;margin:0 0 10px;">${escapeHtml(stage.stageName)}</h2>
          ${lessons}
        </section>`;
    })
    .join("");

  const summaryHtml = `
    <section style="page-break-before:always;">
      <h2 style="color:#1E2761;font-size:20px;border-bottom:2px solid #1E2761;padding-bottom:6px;">סיכום</h2>
      <h3 style="font-size:14px;color:#1E2761;margin-top:12px;">לקחים מרכזיים</h3>
      ${top5.length === 0 ? `<div style="color:#888;">אין נתונים להצגה.</div>` :
        `<ol style="padding-inline-start:20px;font-size:12px;line-height:1.8;">${top5.map((l) => `<li>${escapeHtml(l.title)} — ניקוד התאמה ${l.finalScore.toFixed(0)}</li>`).join("")}</ol>`}
      <h3 style="font-size:14px;color:#1E2761;margin-top:12px;">נושאים חוזרים</h3>
      ${recurring.length === 0 ? `<div style="color:#888;">לא זוהו נושאים חוזרים.</div>` :
        `<ul style="padding-inline-start:20px;font-size:12px;line-height:1.8;">${recurring.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`}
      <h3 style="font-size:14px;color:#1E2761;margin-top:12px;">לקחים באיכות גבוהה (ניקוד ≥ 1.5)</h3>
      ${highQuality.length === 0 ? `<div style="color:#888;">לא נמצאו לקחים באיכות גבוהה ברשימה.</div>` :
        `<ul style="padding-inline-start:20px;font-size:12px;line-height:1.8;">${highQuality.map((l) => `<li>${escapeHtml(l.title)} — ${formatScore(l.quality?.shrunk)}</li>`).join("")}</ul>`}
    </section>`;

  return `
    <div dir="rtl" lang="he" style="font-family: 'Arial', 'Segoe UI', 'Helvetica Neue', sans-serif; width: 794px; padding: 32px; background:#fff; color:#212121; box-sizing:border-box;">
      <header style="text-align:center;background:#1E2761;color:#fff;padding:36px 16px;border-radius:8px;margin-bottom:24px;">
        <div style="font-size:26px;font-weight:700;">לקחים מומלצים לפרויקט</div>
        <div style="font-size:20px;margin-top:8px;">${escapeHtml(payload.project.name)}</div>
        <div style="font-size:12px;margin-top:12px;opacity:.85;">
          סוג פרויקט: ${escapeHtml(payload.project.projectType)} · סוג תחנה: ${escapeHtml(payload.project.stationType)}
          ${payload.project.site ? ` · אתר: ${escapeHtml(payload.project.site)}` : ""}
        </div>
        <div style="font-size:12px;opacity:.85;">תאריך הפקה: ${escapeHtml(payload.generatedAt)}</div>
      </header>
      <section style="margin-bottom:18px;">
        <h2 style="color:#1E2761;font-size:18px;border-bottom:2px solid #1E2761;padding-bottom:6px;">תקציר</h2>
        <div style="font-size:12px;line-height:1.7;">סה״כ ${payload.totalSelected} לקחים נבחרו עבור הפרויקט.</div>
        <div style="font-size:12px;line-height:1.7;color:#444;margin-top:6px;">
          הלקחים נבחרו אוטומטית לפי שילוב של: התאמת שלב, חפיפת ציוד, סוג פרויקט, סוג תחנה, קטגוריה, רמת סיכון, ניקוד איכות מצטבר ואחוז יישום.
        </div>
      </section>
      ${stageHtml}
      ${summaryHtml}
    </div>`;
}

export async function exportRecommendedLessonsToPDF(payload: DocPayload, fileName: string) {
  const host = document.createElement("div");
  host.style.position = "fixed";
  host.style.left = "-10000px";
  host.style.top = "0";
  host.innerHTML = buildPdfHtml(payload);
  document.body.appendChild(host);
  try {
    const target = host.firstElementChild as HTMLElement;
    const canvas = await html2canvas(target, { scale: 2, useCORS: true, logging: false, backgroundColor: "#ffffff" });
    const pdf = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
    const pageW = 210;
    const pageH = 297;
    const imgW = pageW;
    const imgH = (canvas.height * imgW) / canvas.width;
    const imgData = canvas.toDataURL("image/png");
    let heightLeft = imgH;
    let position = 0;
    pdf.addImage(imgData, "PNG", 0, position, imgW, imgH);
    heightLeft -= pageH;
    while (heightLeft > 0) {
      position = heightLeft - imgH;
      pdf.addPage();
      pdf.addImage(imgData, "PNG", 0, position, imgW, imgH);
      heightLeft -= pageH;
    }
    pdf.save(fileName);
  } finally {
    host.remove();
  }
}

// ============== DOCX Export ==============
// Hebrew fix: use object-form font with `cs` (complex-script) explicitly, and
// mark bold/size with `*ComplexScript: true` so Hebrew runs render styled.

const HE_FONT = { ascii: "Arial", cs: "Arial", hAnsi: "Arial" } as const;

// Brand colors aligned with PDF
const BRAND = "1E2761";
const BRAND_LIGHT = "EAEEF7";
const BORDER = "D7DCE8";
const MUTED = "666666";

// A4 portrait, 1000 twip margins → content width ≈ 9920 twips
const CONTENT_W = 9920;

const NO_BORDERS = {
  top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  insideHorizontal: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
  insideVertical: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
};

const CARD_BORDERS = {
  top: { style: BorderStyle.SINGLE, size: 4, color: BORDER },
  bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER },
  left: { style: BorderStyle.SINGLE, size: 4, color: BORDER },
  right: { style: BorderStyle.SINGLE, size: 4, color: BORDER },
};

function heRun(text: string, opts?: { bold?: boolean; size?: number; color?: string }) {
  return new TextRun({
    text,
    rightToLeft: true,
    bold: opts?.bold,
    boldComplexScript: opts?.bold,
    size: opts?.size,
    sizeComplexScript: opts?.size,
    color: opts?.color,
    font: HE_FONT,
    language: { value: "he-IL", bidirectional: "he-IL" },
  });
}

function hePara(
  text: string,
  opts?: {
    bold?: boolean;
    size?: number;
    color?: string;
    heading?: (typeof HeadingLevel)[keyof typeof HeadingLevel];
    align?: (typeof AlignmentType)[keyof typeof AlignmentType];
    spacingAfter?: number;
    spacingBefore?: number;
  },
) {
  return new Paragraph({
    bidirectional: true,
    alignment: opts?.align ?? AlignmentType.RIGHT,
    heading: opts?.heading,
    spacing: { after: opts?.spacingAfter ?? 80, before: opts?.spacingBefore ?? 0 },
    children: [heRun(text, opts)],
  });
}

function bannerTable(text: string, opts?: { size?: number; fill?: string; color?: string }) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    borders: NO_BORDERS,
    rows: [
      new TableRow({
        children: [
          new TableCell({
            width: { size: CONTENT_W, type: WidthType.DXA },
            shading: { fill: opts?.fill ?? BRAND, type: ShadingType.CLEAR, color: "auto" },
            margins: { top: 200, bottom: 200, left: 240, right: 240 },
            verticalAlign: VerticalAlign.CENTER,
            children: [
              hePara(text, {
                bold: true,
                size: opts?.size ?? 28,
                color: opts?.color ?? "FFFFFF",
                align: AlignmentType.CENTER,
                spacingAfter: 0,
              }),
            ],
          }),
        ],
      }),
    ],
  });
}

function spacer(height = 120) {
  return new Paragraph({ spacing: { after: height }, children: [new TextRun({ text: "" })] });
}

function lessonCard(index: number, l: ScoredLesson) {
  const inner: Paragraph[] = [];
  inner.push(hePara(`${index}. ${l.title}`, { bold: true, size: 26, color: BRAND, spacingAfter: 100 }));
  inner.push(hePara(buildSummary(l.description), { size: 22, spacingAfter: 80 }));
  if (l.recommendation) {
    inner.push(
      new Paragraph({
        bidirectional: true,
        alignment: AlignmentType.RIGHT,
        spacing: { after: 80 },
        children: [
          heRun("המלצה: ", { bold: true, size: 22, color: BRAND }),
          heRun(buildSummary(l.recommendation), { size: 22 }),
        ],
      }),
    );
  }
  const metaParts: string[] = [];
  if (l.category) metaParts.push(`קטגוריה: ${l.category}`);
  if (l.professionalDomain) metaParts.push(`תחום: ${l.professionalDomain}`);
  metaParts.push(`ניקוד איכות: ${formatScore(l.quality?.shrunk)}`);
  metaParts.push(`אחוז יישום: ${formatPct(l.quality?.implementationRate)}`);
  metaParts.push(`ניקוד התאמה: ${l.finalScore.toFixed(0)}`);
  inner.push(hePara(metaParts.join(" · "), { size: 20, color: MUTED, spacingAfter: 0 }));

  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    borders: {
      ...CARD_BORDERS,
      insideHorizontal: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
      insideVertical: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
    },
    rows: [
      new TableRow({
        children: [
          new TableCell({
            width: { size: CONTENT_W, type: WidthType.DXA },
            shading: { fill: "FFFFFF", type: ShadingType.CLEAR, color: "auto" },
            margins: { top: 160, bottom: 160, left: 200, right: 200 },
            children: inner,
          }),
        ],
      }),
    ],
  });
}

export async function exportRecommendedLessonsToDOCX(payload: DocPayload, fileName: string) {
  const children: (Paragraph | Table)[] = [];

  // ===== Cover banner =====
  const coverInner: Paragraph[] = [
    hePara("לקחים מומלצים לפרויקט", {
      bold: true, size: 44, color: "FFFFFF", align: AlignmentType.CENTER, spacingAfter: 120,
    }),
    hePara(payload.project.name, {
      bold: true, size: 56, color: "FFFFFF", align: AlignmentType.CENTER, spacingAfter: 200,
    }),
    hePara(
      `סוג פרויקט: ${payload.project.projectType} · סוג תחנה: ${payload.project.stationType}` +
        (payload.project.site ? ` · אתר: ${payload.project.site}` : ""),
      { size: 22, color: "FFFFFF", align: AlignmentType.CENTER, spacingAfter: 60 },
    ),
    hePara(`תאריך הפקה: ${payload.generatedAt}`, {
      size: 22, color: "FFFFFF", align: AlignmentType.CENTER, spacingAfter: 0,
    }),
  ];
  children.push(
    new Table({
      width: { size: CONTENT_W, type: WidthType.DXA },
      columnWidths: [CONTENT_W],
      borders: NO_BORDERS,
      rows: [
        new TableRow({
          children: [
            new TableCell({
              width: { size: CONTENT_W, type: WidthType.DXA },
              shading: { fill: BRAND, type: ShadingType.CLEAR, color: "auto" },
              margins: { top: 600, bottom: 600, left: 300, right: 300 },
              verticalAlign: VerticalAlign.CENTER,
              children: coverInner,
            }),
          ],
        }),
      ],
    }),
  );
  children.push(spacer(200));

  // ===== Intro / abstract =====
  children.push(bannerTable("תקציר", { size: 28 }));
  children.push(spacer(120));
  children.push(hePara(`סה"כ ${payload.totalSelected} לקחים נבחרו עבור הפרויקט.`, { size: 24 }));
  children.push(hePara(
    "הלקחים נבחרו אוטומטית לפי שילוב של: התאמת שלב, חפיפת ציוד, סוג פרויקט, סוג תחנה, קטגוריה, רמת סיכון, ניקוד איכות מצטבר ואחוז יישום.",
    { size: 22 },
  ));
  children.push(hePara(
    "מקרא: ניקוד איכות מוצג בסקלת 0..2 (כולל החלקת Bayes). אחוז יישום מתבסס על תגובות רפרנטים שיושמו בפועל.",
    { size: 20, color: MUTED },
  ));
  children.push(new Paragraph({ children: [new PageBreak()] }));

  // ===== Stages =====
  for (let s = 0; s < payload.stages.length; s++) {
    const stage = payload.stages[s];
    children.push(bannerTable(stage.stageName, { size: 30 }));
    children.push(spacer(160));

    if (stage.lessons.length === 0) {
      children.push(
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: [CONTENT_W],
          borders: CARD_BORDERS,
          rows: [
            new TableRow({
              children: [
                new TableCell({
                  width: { size: CONTENT_W, type: WidthType.DXA },
                  shading: { fill: BRAND_LIGHT, type: ShadingType.CLEAR, color: "auto" },
                  margins: { top: 160, bottom: 160, left: 200, right: 200 },
                  children: [hePara("אין לקחים מומלצים לשלב זה כרגע", { size: 22, color: MUTED, spacingAfter: 0 })],
                }),
              ],
            }),
          ],
        }),
      );
    } else {
      stage.lessons.forEach((l, i) => {
        children.push(lessonCard(i + 1, l));
        children.push(spacer(120));
      });
    }

    if (s < payload.stages.length - 1) {
      children.push(new Paragraph({ children: [new PageBreak()] }));
    }
  }

  // ===== Summary =====
  children.push(new Paragraph({ children: [new PageBreak()] }));
  const { top5, recurring, highQuality } = buildSummarySection(payload);
  children.push(bannerTable("סיכום", { size: 30 }));
  children.push(spacer(160));

  children.push(hePara("לקחים מרכזיים", { heading: HeadingLevel.HEADING_2, bold: true, size: 26, color: BRAND }));
  if (top5.length === 0) {
    children.push(hePara("אין נתונים להצגה.", { size: 22, color: MUTED }));
  } else {
    top5.forEach((l, i) =>
      children.push(hePara(`${i + 1}. ${l.title} — ניקוד התאמה ${l.finalScore.toFixed(0)}`, { size: 22 })),
    );
  }
  children.push(spacer(120));

  children.push(hePara("נושאים חוזרים", { heading: HeadingLevel.HEADING_2, bold: true, size: 26, color: BRAND }));
  if (recurring.length === 0) {
    children.push(hePara("לא זוהו נושאים חוזרים.", { size: 22, color: MUTED }));
  } else {
    recurring.forEach((r) => children.push(hePara("• " + r, { size: 22 })));
  }
  children.push(spacer(120));

  children.push(hePara("לקחים באיכות גבוהה (ניקוד ≥ 1.5)", { heading: HeadingLevel.HEADING_2, bold: true, size: 26, color: BRAND }));
  if (highQuality.length === 0) {
    children.push(hePara("לא נמצאו לקחים באיכות גבוהה ברשימה.", { size: 22, color: MUTED }));
  } else {
    highQuality.forEach((l) =>
      children.push(hePara(`• ${l.title} — ${formatScore(l.quality?.shrunk)}`, { size: 22 })),
    );
  }

  const doc = new Document({
    styles: {
      default: {
        document: { run: { font: HE_FONT, size: 22, rightToLeft: true } },
      },
    },
    sections: [{
      properties: {
        page: {
          margin: { top: 1000, right: 1000, bottom: 1000, left: 1000 },
        },
      },
      children,
    }],
  });

  const blob = await Packer.toBlob(doc);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
