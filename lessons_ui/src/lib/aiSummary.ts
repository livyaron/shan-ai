// The dashboard AI summary arrives as prose in the format the server prompt
// asks for (app/services/lessons_ai.py SUMMARY_FORMAT): "<emoji> **title**"
// headings, then lines, then "ציון ביטחון: X%". This turns it into sections
// the page can lay out as cards. Pure, so it is tested without a model.

export interface SummarySection {
  icon: string;
  title: string;
  lines: string[];
}

export interface ParsedSummary {
  sections: SummarySection[];
  confidence: number | null;
  /** The reply never reached its closing confidence line: it was cut short. */
  incomplete: boolean;
}

const HEADING = /^\s*(?:#{1,4}\s*)?(\p{Extended_Pictographic}[️‍\p{Extended_Pictographic}]*)?\s*\*\*(.+?)\*\*\s*:?\s*$/u;
const MD_HEADING = /^\s*#{1,4}\s*(\p{Extended_Pictographic}[️‍\p{Extended_Pictographic}]*)?\s*(.+?)\s*$/u;
const CONFIDENCE = /ציון\s*ביטחון\s*[:：]?\s*\**\s*(\d{1,3})\s*%/u;

export function parseSummary(text: string): ParsedSummary {
  const sections: SummarySection[] = [];
  let confidence: number | null = null;
  let current: SummarySection | null = null;

  for (const raw of (text || "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const conf = line.match(CONFIDENCE);
    if (conf) {
      confidence = Math.min(100, Number(conf[1]));
      continue;
    }
    const h = line.match(HEADING) || line.match(MD_HEADING);
    if (h) {
      current = { icon: h[1] || "", title: h[2].replace(/\*\*/g, "").trim(), lines: [] };
      sections.push(current);
      continue;
    }
    if (!current) {
      current = { icon: "", title: "", lines: [] };
      sections.push(current);
    }
    current.lines.push(line);
  }
  return { sections, confidence, incomplete: confidence === null };
}

export interface InlinePart {
  text: string;
  bold: boolean;
}

/** "**a** b" → [{a, bold}, {b}]; a stray unmatched "**" stays as text. */
export function inlineParts(line: string): InlinePart[] {
  const parts: InlinePart[] = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) parts.push({ text: line.slice(last, m.index), bold: false });
    parts.push({ text: m[1], bold: true });
    last = m.index + m[0].length;
  }
  if (last < line.length) parts.push({ text: line.slice(last), bold: false });
  return parts;
}

/** A list line ("- x", "• x", "1. x", "* x") → its text, else null. */
export function bulletText(line: string): string | null {
  const m = line.match(/^(?:[-•*▪◦]|\d{1,2}[.)])\s+(.*)$/u);
  return m ? m[1] : null;
}

export type SummaryBlock =
  | { kind: "line"; text: string }
  | { kind: "table"; header: string[]; rows: string[][] };

const TABLE_ROW = /^\|.*\|$/;
const TABLE_RULE = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

const cells = (row: string): string[] => row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

/** Consecutive "| a | b |" lines become one table (the "|---|" rule is
 *  dropped); every other line stays a line. The model writes tables
 *  unprompted (user screenshot 2026-10-17: the risk split as raw pipes). */
export function groupBlocks(lines: string[]): SummaryBlock[] {
  const out: SummaryBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    if (!TABLE_ROW.test(lines[i])) {
      out.push({ kind: "line", text: lines[i] });
      i += 1;
      continue;
    }
    const run: string[] = [];
    while (i < lines.length && TABLE_ROW.test(lines[i])) {
      if (!TABLE_RULE.test(lines[i])) run.push(lines[i]);
      i += 1;
    }
    const [head, ...body] = run.map(cells);
    out.push({ kind: "table", header: head || [], rows: body });
  }
  return out;
}
