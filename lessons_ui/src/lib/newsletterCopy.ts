import type { PeriodRange } from "./newsletterPeriods";

export const managerialMessages = [
  "תקופה של למידה ארגונית — תודה לכל מי שתרם ידע, חוויה והמלצה. כל לקח שמוזן הופך לערך אמיתי בשטח.",
  "המערכת חיה ופועמת בזכותכם. הידע שנצבר כאן הופך אתגרים של פרויקט אחד להזדמנויות לכל הארגון.",
  "שיתוף ידע הוא לב הצלחת הפרויקטים. תודה למובילים — ולכל מי שמצטרף אלינו ומתחיל לתעד את הניסיון שלו.",
];

export const pickManagerialMessage = (range: PeriodRange): string => {
  let h = 0;
  for (const c of range.key) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return managerialMessages[h % managerialMessages.length];
};

export const encouragementCopy = (activePct: number): string => {
  if (activePct >= 0.7) return "שיעור השתתפות מצוין בתקופה זו — הצטרפו גם אתם ותעדו לקח אחד השבוע.";
  if (activePct >= 0.4) return "השימוש במערכת בצמיחה — כל משתמש נוסף שמתעד לקח מוסיף ערך לארגון כולו.";
  return "יש עוד מקום לצמיחה — כל לקח שתתעדו עוזר לפרויקטים אחרים ללמוד מהניסיון שלכם.";
};

/**
 * Actionable deep insights — content layer only.
 * An insight is published ONLY when the underlying lessons prove:
 *   (1) a real recurring event, (2) a clear location, (3) enough occurrences,
 *   and (4) a concrete recommendation that was written by the people in the field.
 * Nothing here is invented: every sentence is composed from real lesson records.
 */
export interface ActionableInsight {
  key: string;
  /** What happened, where, and in how many projects. */
  sentence: string;
  /** Number of lessons the insight is based on (always >= 2). */
  count: number;
  /** Measured impact taken from the lessons (delay days / cost / quality). */
  impact?: string;
  /** A real lesson title that demonstrates the pattern. */
  example?: string;
  /** Concrete recommendation, sourced from the lessons themselves. */
  recommendation: string;
}

const MIN_INSIGHT_LESSONS = 2;
const MAX_INSIGHTS = 4;
const MIN_RECOMMENDATION_LENGTH = 15;

/** Generic phrasings that carry no operational value — such recommendations are rejected. */
const GENERIC_RECOMMENDATION_PATTERNS = [
  /^שפרו\b/,
  /^יש לשפר\b/,
  /^בדקו\b/,
  /^הוסיפו\b/,
  /^יש להוסיף סעיף\b/,
  /^להקפיד\b/,
  /^שימו לב\b/,
];

/** Closing call-to-action for the deep-insights zone. */
export const INSIGHTS_CTA =
  "רוצים לבדוק אם זה רלוונטי לפרויקט שלכם? היכנסו למערכת וצפו בלקחים";

interface EvidenceClusterInput {
  key: string;
  cause: string;
  where: string;
  whereKind: "stage" | "domain";
  count: number;
  projectCount: number;
  delayDays: number;
  budgetCost: number;
  qualityImpact?: string;
  exampleTitle: string;
  recommendations: string[];
  highSeverityCount: number;
}

interface InsightSource {
  evidenceClusters: EvidenceClusterInput[];
}

const isGeneric = (text: string) =>
  GENERIC_RECOMMENDATION_PATTERNS.some((pattern) => pattern.test(text));

/** Picks the most specific real recommendation written on the lessons of the cluster. */
const pickRecommendation = (cluster: EvidenceClusterInput): string | null => {
  const candidates = cluster.recommendations
    .map((r) => r.replace(/\s+/g, " ").trim())
    .filter((r) => r.length >= MIN_RECOMMENDATION_LENGTH && !isGeneric(r));
  if (candidates.length === 0) return null;

  const mentionsContext = candidates.filter(
    (r) => r.includes(cluster.where) || r.includes(cluster.cause)
  );
  const pool = mentionsContext.length > 0 ? mentionsContext : candidates;
  const best = pool.reduce((a, b) => (b.length > a.length ? b : a));
  return best.length > 220 ? `${best.slice(0, 217)}...` : best;
};

/** Builds the measured-impact clause strictly from recorded lesson values. */
const buildImpact = (cluster: EvidenceClusterInput): string | undefined => {
  const parts: string[] = [];
  if (cluster.delayDays > 0) parts.push(`${cluster.delayDays} ימי עיכוב בלוח הזמנים`);
  if (cluster.budgetCost > 0)
    parts.push(`עלות נוספת של ${cluster.budgetCost.toLocaleString("he-IL")} ₪`);
  if (cluster.qualityImpact) parts.push(`פגיעה באיכות: ${cluster.qualityImpact}`);
  return parts.length > 0 ? parts.join(" · ") : undefined;
};

export const buildActionableInsights = (stats: InsightSource): ActionableInsight[] => {
  const clusters = stats.evidenceClusters ?? [];

  const insights = clusters
    .filter((cluster) => cluster.count >= MIN_INSIGHT_LESSONS)
    .map((cluster) => {
      const recommendation = pickRecommendation(cluster);
      if (!recommendation) return null; // no real recommendation => no insight

      const whereLabel = cluster.whereKind === "stage" ? "שלב" : "תחום";
      const scope =
        cluster.projectCount > 1
          ? `ב-${cluster.projectCount} פרויקטים שונים`
          : "באותו פרויקט, ביותר ממקרה אחד";
      const impact = buildImpact(cluster);

      const insight: ActionableInsight & { severity: number } = {
        key: cluster.key,
        sentence: `${cluster.cause} התרחשה ב${whereLabel} ${cluster.where} ${scope}`,
        count: cluster.count,
        impact,
        example: cluster.exampleTitle,
        recommendation,
        severity:
          cluster.highSeverityCount * 2 +
          (cluster.delayDays > 0 ? 2 : 0) +
          (cluster.budgetCost > 0 ? 1 : 0) +
          (cluster.projectCount > 1 ? 2 : 0),
      };
      return insight;
    })
    .filter(Boolean) as (ActionableInsight & { severity: number })[];

  insights.sort((a, b) => b.count - a.count || b.severity - a.severity);

  return insights
    .slice(0, MAX_INSIGHTS)
    .map(({ severity: _severity, ...insight }) => insight);
};


