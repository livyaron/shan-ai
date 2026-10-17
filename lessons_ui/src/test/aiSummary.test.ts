import { describe, expect, it } from "vitest";
import { bulletText, groupBlocks, inlineParts, parseSummary } from "@/lib/aiSummary";

const FULL = `📈 **מגמות מפתח**
- רוב הלקחים בשלב **תכנון**
⚠️ **סיכונים**
1. בידוד שנאים (גבוה)

🎯 **Top 3 עדיפויות**
- א
ציון ביטחון: 78%`;

describe("parseSummary", () => {
  it("splits the server format into sections and reads the confidence", () => {
    const p = parseSummary(FULL);
    expect(p.sections.map((s) => s.title)).toEqual(["מגמות מפתח", "סיכונים", "Top 3 עדיפויות"]);
    expect(p.sections[0].icon).toBe("📈");
    expect(p.sections[1].lines).toEqual(["1. בידוד שנאים (גבוה)"]);
    expect(p.confidence).toBe(78);
    expect(p.incomplete).toBe(false);
  });

  it("marks a reply that never reached its confidence line as cut short", () => {
    const p = parseSummary("📈 **מגמות מפתח**\n- רוב הלקחים");
    expect(p.incomplete).toBe(true);
    expect(p.sections[0].lines).toEqual(["- רוב הלקחים"]);
  });

  it("keeps text with no headings instead of dropping it", () => {
    const p = parseSummary("שורה אחת\nשורה שתיים");
    expect(p.sections).toHaveLength(1);
    expect(p.sections[0].title).toBe("");
    expect(p.sections[0].lines).toHaveLength(2);
  });

  it("reads markdown headings too", () => {
    expect(parseSummary("## ⚠️ סיכונים\nx").sections[0]).toEqual({ icon: "⚠️", title: "סיכונים", lines: ["x"] });
  });
});

describe("inline helpers", () => {
  it("bolds **pairs** and leaves a stray marker as text", () => {
    expect(inlineParts("a **b** c")).toEqual([
      { text: "a ", bold: false }, { text: "b", bold: true }, { text: " c", bold: false },
    ]);
    expect(inlineParts("a ** b")).toEqual([{ text: "a ** b", bold: false }]);
  });

  it("recognises list lines", () => {
    expect(bulletText("- x")).toBe("x");
    expect(bulletText("2. y")).toBe("y");
    expect(bulletText("רגיל")).toBeNull();
  });
});

describe("groupBlocks", () => {
  it("turns a markdown table (with blank lines between rows) into one table", () => {
    const text = `⚠️ **סיכונים**
| רמת סיכון | מספר לקחים | אחוז מהסך |

|-----------|------------|-----------|

| גבוה | 13 | 29 % |

| נמוך | 7 | 16 % |

הסיכון הגבוה מרוכז בעיקר בתכנון חפירות.`;
    const blocks = groupBlocks(parseSummary(text).sections[0].lines);
    expect(blocks).toEqual([
      { kind: "table", header: ["רמת סיכון", "מספר לקחים", "אחוז מהסך"], rows: [["גבוה", "13", "29 %"], ["נמוך", "7", "16 %"]] },
      { kind: "line", text: "הסיכון הגבוה מרוכז בעיקר בתכנון חפירות." },
    ]);
  });
});
