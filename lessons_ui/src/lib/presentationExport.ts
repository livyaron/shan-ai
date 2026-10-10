import PptxGenJS from "pptxgenjs";
import jsPDF from "jspdf";

export interface PresentationLesson {
  id: number;
  title: string;
  description: string;
  recommendation?: string;
  stage: string;
  category?: string;
  date?: string;
  eventDate?: string;
}

export interface PresentationStage {
  stageName: string;
  lessons: PresentationLesson[];
}

export interface PresentationData {
  projectName: string;
  generatedAt: string; // formatted Hebrew date
  stages: PresentationStage[];
}

const SUMMARY_MAX = 220;

export function buildSummary(text: string, max: number = SUMMARY_MAX): string {
  const clean = (text || "").replace(/\s+/g, " ").trim();
  if (!clean) return "";
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return cut.slice(0, lastSpace > 0 ? lastSpace : max).trimEnd() + "…";
}

/** Sort lessons newest first: eventDate → date → id (desc). */
export function sortLessonsForPresentation<T extends PresentationLesson>(lessons: T[]): T[] {
  const ts = (s?: string) => {
    if (!s) return -Infinity;
    const t = new Date(s).getTime();
    return Number.isFinite(t) ? t : -Infinity;
  };
  return [...lessons].sort((a, b) => {
    const ea = ts(a.eventDate);
    const eb = ts(b.eventDate);
    if (ea !== eb) return eb - ea;
    const da = ts(a.date);
    const db = ts(b.date);
    if (da !== db) return db - da;
    return b.id - a.id;
  });
}

/** Group lessons strictly by lesson.stage; lessons not matching any active stage go to "ללא שלב משויך". */
export function groupLessonsByStage<T extends PresentationLesson>(
  lessons: T[],
  orderedStages: string[],
): { stageName: string; lessons: T[] }[] {
  const groups = new Map<string, T[]>();
  orderedStages.forEach((s) => groups.set(s, []));
  const unassigned: T[] = [];
  for (const l of lessons) {
    if (l.stage && groups.has(l.stage)) {
      groups.get(l.stage)!.push(l);
    } else {
      unassigned.push(l);
    }
  }
  const result = orderedStages
    .map((s) => ({ stageName: s, lessons: sortLessonsForPresentation(groups.get(s) || []) }))
    .filter((g) => g.lessons.length > 0);
  if (unassigned.length > 0) {
    result.push({ stageName: "ללא שלב משויך", lessons: sortLessonsForPresentation(unassigned) });
  }
  return result;
}

export async function exportPresentationToPPTX(data: PresentationData, fileName: string) {
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
  pptx.rtlMode = true;

  // Cover slide
  const cover = pptx.addSlide();
  cover.background = { color: "1E2761" };
  cover.addText("לקחי פרויקט", {
    x: 0.5, y: 2.5, w: 12.3, h: 0.8,
    fontFace: "Calibri", fontSize: 28, color: "CADCFC", align: "center", rtlMode: true,
  });
  cover.addText(data.projectName, {
    x: 0.5, y: 3.3, w: 12.3, h: 1.2,
    fontFace: "Calibri", fontSize: 44, bold: true, color: "FFFFFF", align: "center", rtlMode: true,
  });
  cover.addText(`הופק: ${data.generatedAt}`, {
    x: 0.5, y: 6.5, w: 12.3, h: 0.4,
    fontFace: "Calibri", fontSize: 14, color: "CADCFC", align: "center", rtlMode: true,
  });

  for (const stage of data.stages) {
    // Stage divider
    const div = pptx.addSlide();
    div.background = { color: "F5F5F5" };
    div.addShape("rect", { x: 0, y: 3, w: 13.33, h: 1.5, fill: { color: "1E2761" } });
    div.addText(stage.stageName, {
      x: 0.5, y: 3.1, w: 12.3, h: 1.3,
      fontFace: "Calibri", fontSize: 36, bold: true, color: "FFFFFF", align: "center", rtlMode: true,
    });
    div.addText(`${stage.lessons.length} לקחים`, {
      x: 0.5, y: 4.7, w: 12.3, h: 0.5,
      fontFace: "Calibri", fontSize: 18, color: "1E2761", align: "center", rtlMode: true,
    });

    for (const lesson of stage.lessons) {
      const slide = pptx.addSlide();
      slide.background = { color: "FFFFFF" };
      // header bar
      slide.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.6, fill: { color: "1E2761" } });
      slide.addText(stage.stageName, {
        x: 0.4, y: 0.05, w: 12.5, h: 0.5,
        fontFace: "Calibri", fontSize: 14, color: "CADCFC", align: "right", rtlMode: true,
      });

      slide.addText(lesson.title, {
        x: 0.5, y: 0.9, w: 12.3, h: 1.0,
        fontFace: "Calibri", fontSize: 28, bold: true, color: "1E2761", align: "right", rtlMode: true,
      });

      slide.addText("תיאור", {
        x: 0.5, y: 2.0, w: 12.3, h: 0.4,
        fontFace: "Calibri", fontSize: 16, bold: true, color: "1E2761", align: "right", rtlMode: true,
      });
      slide.addText(buildSummary(lesson.description), {
        x: 0.5, y: 2.4, w: 12.3, h: 2.0,
        fontFace: "Calibri", fontSize: 16, color: "212121", align: "right", rtlMode: true, valign: "top",
      });

      if (lesson.recommendation) {
        slide.addText("המלצה", {
          x: 0.5, y: 4.5, w: 12.3, h: 0.4,
          fontFace: "Calibri", fontSize: 16, bold: true, color: "1E2761", align: "right", rtlMode: true,
        });
        slide.addText(buildSummary(lesson.recommendation), {
          x: 0.5, y: 4.9, w: 12.3, h: 1.8,
          fontFace: "Calibri", fontSize: 16, color: "212121", align: "right", rtlMode: true, valign: "top",
        });
      }

      const meta: string[] = [];
      if (lesson.category) meta.push(lesson.category);
      if (lesson.eventDate) meta.push(`אירוע: ${lesson.eventDate}`);
      else if (lesson.date) meta.push(`תאריך: ${lesson.date}`);
      slide.addText(meta.join(" · "), {
        x: 0.5, y: 7.0, w: 12.3, h: 0.4,
        fontFace: "Calibri", fontSize: 12, color: "666666", align: "right", rtlMode: true,
      });
    }
  }

  await pptx.writeFile({ fileName });
}

/**
 * Lightweight PDF export — Hebrew RTL. Uses default font; for full Hebrew
 * shaping users may prefer the PPTX export. Provides basic, readable layout.
 */
export function exportPresentationToPDF(data: PresentationData, fileName: string) {
  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 40;

  const addRTL = (text: string, x: number, y: number, opts?: { size?: number; bold?: boolean; color?: [number, number, number] }) => {
    doc.setFontSize(opts?.size ?? 14);
    doc.setFont("helvetica", opts?.bold ? "bold" : "normal");
    if (opts?.color) doc.setTextColor(...opts.color);
    else doc.setTextColor(33, 33, 33);
    doc.text(text, x, y, { align: "right" });
  };

  // Cover
  doc.setFillColor(30, 39, 97);
  doc.rect(0, 0, pageW, pageH, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(36);
  doc.text(data.projectName, pageW / 2, pageH / 2, { align: "center" });
  doc.setFontSize(18);
  doc.setFont("helvetica", "normal");
  doc.text("לקחי פרויקט", pageW / 2, pageH / 2 - 50, { align: "center" });
  doc.setFontSize(12);
  doc.text(`הופק: ${data.generatedAt}`, pageW / 2, pageH - 40, { align: "center" });

  for (const stage of data.stages) {
    doc.addPage();
    doc.setFillColor(245, 245, 245);
    doc.rect(0, 0, pageW, pageH, "F");
    doc.setFillColor(30, 39, 97);
    doc.rect(0, pageH / 2 - 60, pageW, 120, "F");
    doc.setTextColor(255, 255, 255);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(28);
    doc.text(stage.stageName, pageW / 2, pageH / 2 + 5, { align: "center" });
    doc.setFontSize(14);
    doc.setFont("helvetica", "normal");
    doc.text(`${stage.lessons.length} לקחים`, pageW / 2, pageH / 2 + 30, { align: "center" });

    for (const lesson of stage.lessons) {
      doc.addPage();
      // header bar
      doc.setFillColor(30, 39, 97);
      doc.rect(0, 0, pageW, 30, "F");
      doc.setTextColor(202, 220, 252);
      doc.setFontSize(11);
      doc.text(stage.stageName, pageW - margin, 20, { align: "right" });

      addRTL(lesson.title, pageW - margin, 70, { size: 22, bold: true, color: [30, 39, 97] });
      addRTL("תיאור", pageW - margin, 110, { size: 13, bold: true, color: [30, 39, 97] });
      const desc = doc.splitTextToSize(buildSummary(lesson.description), pageW - margin * 2);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(12);
      doc.setTextColor(33, 33, 33);
      desc.forEach((line: string, i: number) => doc.text(line, pageW - margin, 130 + i * 16, { align: "right" }));

      let y = 130 + desc.length * 16 + 20;
      if (lesson.recommendation) {
        addRTL("המלצה", pageW - margin, y, { size: 13, bold: true, color: [30, 39, 97] });
        const rec = doc.splitTextToSize(buildSummary(lesson.recommendation), pageW - margin * 2);
        rec.forEach((line: string, i: number) => doc.text(line, pageW - margin, y + 20 + i * 16, { align: "right" }));
        y = y + 20 + rec.length * 16;
      }

      const meta: string[] = [];
      if (lesson.category) meta.push(lesson.category);
      if (lesson.eventDate) meta.push(`אירוע: ${lesson.eventDate}`);
      else if (lesson.date) meta.push(`תאריך: ${lesson.date}`);
      doc.setFontSize(10);
      doc.setTextColor(102, 102, 102);
      doc.text(meta.join(" · "), pageW - margin, pageH - 30, { align: "right" });
    }
  }

  doc.save(fileName);
}
