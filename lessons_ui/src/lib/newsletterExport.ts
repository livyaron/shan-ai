import jsPDF from "jspdf";
import { RUBIK_REGULAR_BASE64, RUBIK_BOLD_BASE64 } from "./fonts/rubik";
import type { NewsletterStats } from "./newsletterStats";
import { formatDateRange } from "./newsletterPeriods";
import { pickManagerialMessage, buildActionableInsights, INSIGHTS_CTA } from "./newsletterCopy";

/**
 * Programmatic A4 PDF export using jsPDF with an embedded Rubik font (Latin + Hebrew)
 * and native right-to-left rendering. No html2canvas — the output is real, selectable
 * text with embedded fonts, fully RTL.
 */

const A4_W = 595.28;
const A4_H = 841.89;
const MARGIN = 44;
const RIGHT = A4_W - MARGIN;
const CONTENT_W = A4_W - MARGIN * 2;

type RGB = [number, number, number];
const C = {
  text: [30, 41, 59] as RGB,
  muted: [100, 116, 139] as RGB,
  primary: [37, 99, 235] as RGB,
  accent: [217, 119, 6] as RGB,
  danger: [220, 38, 38] as RGB,
  line: [226, 232, 240] as RGB,
};

interface Cursor { y: number; }

const registerFonts = (doc: jsPDF) => {
  doc.addFileToVFS("Rubik-Regular.ttf", RUBIK_REGULAR_BASE64);
  doc.addFont("Rubik-Regular.ttf", "Rubik", "normal");
  doc.addFileToVFS("Rubik-Bold.ttf", RUBIK_BOLD_BASE64);
  doc.addFont("Rubik-Bold.ttf", "Rubik", "bold");
  doc.setFont("Rubik", "normal");
};

const ensureSpace = (doc: jsPDF, cur: Cursor, needed: number) => {
  if (cur.y + needed > A4_H - MARGIN) {
    doc.addPage();
    cur.y = MARGIN;
  }
};

interface TextOpts {
  size?: number;
  style?: "normal" | "bold";
  color?: RGB;
  x?: number;
  align?: "right" | "center" | "left";
  gapAfter?: number;
  maxWidth?: number;
}

const writeText = (doc: jsPDF, cur: Cursor, text: string, opts: TextOpts = {}) => {
  const {
    size = 10, style = "normal", color = C.text,
    x = RIGHT, align = "right", gapAfter = 3, maxWidth = CONTENT_W,
  } = opts;
  doc.setFont("Rubik", style);
  doc.setFontSize(size);
  doc.setTextColor(color[0], color[1], color[2]);
  const lineHeight = size * 1.35;
  const lines = doc.splitTextToSize(text, maxWidth) as string[];
  for (const line of lines) {
    ensureSpace(doc, cur, lineHeight);
    cur.y += lineHeight;
    doc.text(line, x, cur.y, { align });
  }
  cur.y += gapAfter;
};

const sectionHeader = (doc: jsPDF, cur: Cursor, title: string) => {
  ensureSpace(doc, cur, 32);
  cur.y += 10;
  writeText(doc, cur, title, { size: 13, style: "bold", color: C.primary, gapAfter: 2 });
  doc.setDrawColor(C.line[0], C.line[1], C.line[2]);
  doc.setLineWidth(0.8);
  doc.line(MARGIN, cur.y, RIGHT, cur.y);
  cur.y += 6;
};

const bullet = (doc: jsPDF, cur: Cursor, text: string, color: RGB = C.text) => {
  // Right-aligned bullet marker + wrapped text with a slight indent.
  const indent = 14;
  doc.setFont("Rubik", "normal");
  doc.setFontSize(10);
  doc.setTextColor(color[0], color[1], color[2]);
  doc.text("•", RIGHT, cur.y + 13, { align: "right" });
  writeText(doc, cur, text, { x: RIGHT - indent, maxWidth: CONTENT_W - indent, color });
};




export function exportNewsletterToPDF(stats: NewsletterStats, fileName: string): Promise<void> {
  const doc = new jsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
  registerFonts(doc);
  doc.setR2L(true);

  const cur: Cursor = { y: MARGIN };
  const {
    range, kpiCreated, createdApproved, createdPending, createdDraft,
    kpiApproved, kpiImplemented,
    topPMs, topReferents, topImplementers,
    featuredLesson, implementationRate, implementationRateDelta,
    createdPrev, createdDeltaPct,
  } = stats;

  // Header
  writeText(doc, cur, "מערכת הלקחים · עלון פעילות", { size: 9, color: C.muted, align: "center", x: A4_W / 2, gapAfter: 1 });
  writeText(doc, cur, range.labelHe, { size: 20, style: "bold", color: C.primary, align: "center", x: A4_W / 2, gapAfter: 1 });
  writeText(doc, cur, `${formatDateRange(range.start, range.end)} · הופק: ${new Date().toLocaleDateString("he-IL")}`,
    { size: 8.5, color: C.muted, align: "center", x: A4_W / 2, gapAfter: 4 });
  doc.setDrawColor(C.line[0], C.line[1], C.line[2]);
  doc.line(MARGIN, cur.y, RIGHT, cur.y);
  cur.y += 2;

  // Zone 1 — Leaders (hero)
  sectionHeader(doc, cur, "מובילי התקופה");
  const leaderBlock = (title: string, color: RGB, items: { name: string; count: number }[]) => {
    writeText(doc, cur, title, { size: 11, style: "bold", color, gapAfter: 1 });
    if (items.length === 0) {
      writeText(doc, cur, "אין נתונים בתקופה זו", { size: 9, color: C.muted, gapAfter: 4 });
      return;
    }
    for (let i = 0; i < items.length; i++) {
      writeText(doc, cur, `${i + 1}. ${items[i].name} — ${items[i].count}`, { size: 10, gapAfter: 1 });
    }
    cur.y += 3;
  };
  leaderBlock("מנהלי פרויקטים", C.primary, topPMs);
  leaderBlock("רפרנטים", C.accent, topReferents);
  leaderBlock("יישום לקחים בפרויקטים", [5, 150, 105], topImplementers);

  // Zone 2 — KPI
  sectionHeader(doc, cur, "מדדי פעילות");
  writeText(doc, cur,
    `לקחים חדשים: ${kpiCreated}   |   אושרו והופצו: ${kpiApproved}   |   לקחים יושמו בפרויקטים עתידיים / דומים / רלוונטיים: ${kpiImplemented}`,
    { size: 10.5, style: "bold", gapAfter: 4 });
  writeText(doc, cur, `פירוט ${kpiCreated} הלקחים החדשים לפי סטטוס — מאושרים: ${createdApproved} · ממתינים: ${createdPending} · טיוטות: ${createdDraft}`,
    { size: 9.5, color: C.muted, gapAfter: 3 });
  if (createdDeltaPct !== null) {
    writeText(doc, cur, `השוואה לתקופה הקודמת: ${createdDeltaPct >= 0 ? "+" : ""}${Math.round(createdDeltaPct)}% לקחים חדשים (היו ${createdPrev})`,
      { size: 9, color: C.muted });
  }

  // Zone 3 — Deep insights (uniform format)
  sectionHeader(doc, cur, "תובנות עומק");
  const insights = buildActionableInsights(stats);
  if (insights.length > 0) {
    for (const i of insights) {
      writeText(doc, cur, `${i.sentence}  (מבוסס על ${i.count} לקחים)`, { size: 10, style: "bold", gapAfter: 1 });
      if (i.impact) {
        writeText(doc, cur, `השפעה בפועל: ${i.impact}`, { size: 9.5, color: C.muted, gapAfter: 1 });
      }
      if (i.example) {
        writeText(doc, cur, `לדוגמה: ${i.example}`, { size: 9.5, color: C.muted, gapAfter: 1 });
      }
      writeText(doc, cur, `המלצה: ${i.recommendation}`, { size: 9.5, color: C.muted, gapAfter: 4 });
    }

  } else {
    writeText(doc, cur, "עדיין אין מספיק נתונים לזיהוי דפוסים חוזרים בתקופה זו.", { size: 10, color: C.muted, gapAfter: 4 });
  }
  writeText(doc, cur, INSIGHTS_CTA, { size: 10, style: "bold", color: C.primary, gapAfter: 2 });


  if (featuredLesson) {
    cur.y += 4;
    writeText(doc, cur, "לקח התקופה", { size: 10, style: "bold", color: C.accent, gapAfter: 1 });
    writeText(doc, cur, featuredLesson.title, { size: 10, style: "bold", gapAfter: 1 });
    const meta = [
      featuredLesson.projectName ? `פרויקט: ${featuredLesson.projectName}` : null,
      featuredLesson.domain ? `תחום: ${featuredLesson.domain}` : null,
      `מבוסס על ${featuredLesson.implementedCount} יישומים`,
    ].filter(Boolean).join(" · ");
    writeText(doc, cur, meta, { size: 9, color: C.muted });
  }

  cur.y += 4;
  const rateStr = implementationRate !== null ? `${Math.round(implementationRate * 100)}%` : "—";
  const deltaStr = implementationRateDelta !== null
    ? ` (${implementationRateDelta >= 0 ? "+" : ""}${Math.round(implementationRateDelta)} נק' אחוז מהתקופה הקודמת)`
    : "";
  writeText(doc, cur, `מדד יישום בתקופה: ${rateStr}${deltaStr}`, { size: 10, style: "bold" });

  // Zone 4 — call to action
  sectionHeader(doc, cur, "מה אפשר לעשות עכשיו");
  bullet(doc, cur, "הוסיפו לקחים חדשים מהפרויקט שלכם.");
  bullet(doc, cur, "בדקו לקחים רלוונטיים לפרויקטים שאתם מנהלים.");
  bullet(doc, cur, "תנו מענה ללקחים בתחום האחריות שלכם (רפרנטים).");
  cur.y += 6;
  writeText(doc, cur, pickManagerialMessage(range), { size: 10, gapAfter: 2 });

  // Footer on every page
  const pages = doc.getNumberOfPages();
  for (let p = 1; p <= pages; p++) {
    doc.setPage(p);
    doc.setFont("Rubik", "normal");
    doc.setFontSize(7.5);
    doc.setTextColor(C.muted[0], C.muted[1], C.muted[2]);
    doc.text("עלון זה הופק אוטומטית ממערכת הלקחים · להפצה פנים-ארגונית", A4_W / 2, A4_H - 24, { align: "center" });
    doc.text(`${p}/${pages}`, MARGIN, A4_H - 24, { align: "left" });
  }

  doc.save(fileName);
  return Promise.resolve();
}
