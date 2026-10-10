/**
 * Newsletter statistics — pure, deterministic.
 * All counts are by DISTINCT lessonId where applicable.
 *
 * Single source of truth: a "new lesson" is a lesson whose CreatedDate falls in
 * the reporting range. Its status is derived via classifyLesson (see lessonStatus.ts),
 * so the headline count and the status breakdown always add up.
 */
import type { Lesson, LessonImplementation, ReferentReview, MockUser, Project } from "@/context/UserContext";
import type { PeriodRange } from "./newsletterPeriods";
import { classifyLesson } from "./lessonStatus";
import { lessonsCreatedInRange, bucketBreakdown } from "./newsletterQuery";

const parseDate = (s?: string | null): Date | null => {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
};

const inRange = (d: Date | null, from: Date, to: Date) =>
  d !== null && d >= from && d <= to;

const distinct = <T>(xs: T[]) => [...new Set(xs)];

// Keeps the top N, but includes every user tied with the last place (fairness)
const topN = <T extends { count: number }>(items: T[], n: number): T[] => {
  const sorted = [...items].sort((a, b) => b.count - a.count);
  if (sorted.length <= n) return sorted;
  const cutoff = sorted[n - 1].count;
  return sorted.filter((i) => i.count >= cutoff);
};

const aggregateBy = <T>(items: T[], getKey: (i: T) => string | undefined) => {
  const m = new Map<string, number>();
  for (const i of items) {
    const k = getKey(i);
    if (!k) continue;
    m.set(k, (m.get(k) ?? 0) + 1);
  }
  return [...m.entries()].map(([key, count]) => ({ key, count })).sort((a, b) => b.count - a.count);
};

export interface TopUser {
  userId: string;
  name: string;
  roleLabel: string;
  count: number;
}

export interface InsightItem {
  key: string;
  count: number;
}

/**
 * A real, evidence-backed cluster of lessons sharing the same root cause
 * and the same location (stage or professional domain).
 * Every field originates from actual lesson records — nothing is invented.
 */
export interface EvidenceCluster {
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


export interface ProblemDomain {
  key: string;
  count: number;
  avgDelayDays: number | null;
}

export type AttentionTone = "critical" | "warning" | "info";

export interface AttentionItem {
  tone: AttentionTone;
  label: string;
  detail: string;
}

export interface FeaturedLesson {
  id: number;
  title: string;
  projectName?: string;
  domain?: string;
  implementedCount: number;
}

export interface NewsletterStats {
  range: PeriodRange;
  // Zone B — KPI (headline + status breakdown, single source of truth)
  kpiCreated: number;          // total lessons created in range
  createdApproved: number;     // of those, Status = Approved
  createdPending: number;      // of those, still pending
  createdDraft: number;        // of those, drafts
  kpiApproved: number;         // approved & distributed in range (headline "אושרו והופצו")
  kpiImplemented: number;
  kpiNotRelevant: number;
  activeUsersCount: number;
  totalUsersCount: number;
  activePct: number;
  // Zone A — attention (AI-style deterministic highlights)
  attention: AttentionItem[];
  // Zone C — deep insights
  topPMs: TopUser[];
  topReferents: TopUser[];
  topImplementers: TopUser[];
  topCategories: InsightItem[];
  topDomains: InsightItem[];
  topStages: InsightItem[];
  recurringPatterns: InsightItem[];  // RootCause (category) + Milestone (stage)
  evidenceClusters: EvidenceCluster[]; // real lesson-backed clusters for deep insights

  problemDomains: ProblemDomain[];   // domains with high severity / delays
  delayedCount: number;
  delayDaysTotal: number;
  avgDelayDays: number | null;
  featuredLesson: FeaturedLesson | null;
  implementationRate: number | null;
  implementationRatePrev: number | null;
  implementationRateDelta: number | null;
  createdPrev: number;
  createdDeltaPct: number | null;
}

interface ComputeArgs {
  range: PeriodRange;
  lessons: Lesson[];
  implementations: LessonImplementation[];
  reviews: ReferentReview[];
  users: MockUser[];
  projects: Project[];
  roleLabels: Record<string, string>;
}

export const computeNewsletterStats = ({
  range, lessons, implementations, reviews, users, projects, roleLabels,
}: ComputeArgs): NewsletterStats => {
  const { start, end, previous } = range;

  // Single source of truth: everything created in range (shared query helper).
  const lessonsCreated = lessonsCreatedInRange(lessons, range);
  const lessonsApprovedInRange = lessonsCreated.filter((l) => classifyLesson(l) === "approved");

  const implsInPeriod = implementations.filter((i) => inRange(parseDate(i.respondedAt), start, end));
  const reviewsInPeriod = reviews.filter((r) => inRange(parseDate(r.respondedAt), start, end));

  const lessonsCreatedPrev = lessons.filter((l) => inRange(parseDate(l.date), previous.start, previous.end));
  const implsPrev = implementations.filter((i) => inRange(parseDate(i.respondedAt), previous.start, previous.end));

  const kpiCreated = distinct(lessonsCreated.map((l) => l.id)).length;
  const breakdown = bucketBreakdown(lessonsCreated);
  const createdApproved = breakdown.approved;
  const createdPending = breakdown.pending;
  const createdDraft = breakdown.draft;
  const kpiApproved = createdApproved;

  const kpiImplemented = distinct(implsInPeriod.filter((i) => i.isImplemented === true).map((i) => i.lessonId)).length;
  const kpiNotRelevant = distinct(implsInPeriod.filter((i) => i.isRelevant === false).map((i) => i.lessonId)).length;

  const activeIds = new Set<string>();
  lessonsCreated.forEach((l) => l.createdBy && activeIds.add(l.createdBy));
  lessonsApprovedInRange.forEach((l) => l.approvedBy && activeIds.add(l.approvedBy));
  implsInPeriod.forEach((i) => i.respondedBy && activeIds.add(i.respondedBy));
  reviewsInPeriod.forEach((r) => r.referentId && activeIds.add(r.referentId));
  const activeUsersCount = activeIds.size;
  const totalUsersCount = users.length;
  const activePct = totalUsersCount > 0 ? activeUsersCount / totalUsersCount : 0;

  const userById = new Map(users.map((u) => [u.id, u]));
  const toTopUser = (userId: string, count: number): TopUser | null => {
    const u = userById.get(userId);
    if (!u) return null;
    return { userId, name: u.name, roleLabel: roleLabels[u.role] ?? u.role, count };
  };

  const pmCounts = new Map<string, number>();
  for (const l of lessonsCreated) {
    const u = userById.get(l.createdBy);
    if (!u || u.role !== "project_manager") continue;
    pmCounts.set(l.createdBy, (pmCounts.get(l.createdBy) ?? 0) + 1);
  }
  const topPMs = topN(
    [...pmCounts.entries()].map(([id, c]) => toTopUser(id, c)).filter(Boolean) as TopUser[],
    3
  );

  const refCounts = new Map<string, number>();
  for (const r of reviewsInPeriod) {
    refCounts.set(r.referentId, (refCounts.get(r.referentId) ?? 0) + 1);
  }
  const topReferents = topN(
    [...refCounts.entries()].map(([id, c]) => toTopUser(id, c)).filter(Boolean) as TopUser[],
    3
  );

  const impCounts = new Map<string, number>();
  for (const i of implsInPeriod) {
    if (i.isImplemented !== true || !i.respondedBy) continue;
    impCounts.set(i.respondedBy, (impCounts.get(i.respondedBy) ?? 0) + 1);
  }
  const topImplementers = topN(
    [...impCounts.entries()].map(([id, c]) => toTopUser(id, c)).filter(Boolean) as TopUser[],
    3
  );

  const topCategories = aggregateBy(lessonsCreated, (l) => l.category).slice(0, 3);
  const topDomains = aggregateBy(lessonsCreated, (l) => l.professionalDomain).slice(0, 3);
  const topStages = aggregateBy(lessonsCreated, (l) => l.stage).slice(0, 3);

  // Zone C — recurring patterns: RootCause (category) + Milestone (stage), count >= 2
  const recurringPatterns = aggregateBy(
    lessonsCreated,
    (l) => (l.category && l.stage ? `${l.category} · ${l.stage}` : undefined)
  ).filter((p) => p.count >= 2).slice(0, 3);

  // Zone C — evidence clusters: same root cause + same location (stage, else domain).
  // Only real lesson data is aggregated here; the copy layer decides what is publishable.
  const clusterAgg = new Map<string, EvidenceCluster>();
  for (const lesson of lessonsCreated) {
    const cause = lesson.category?.trim();
    if (!cause) continue;
    const stage = lesson.stage?.trim();
    const domain = lesson.professionalDomain?.trim();
    const where = stage || domain;
    if (!where) continue;
    const whereKind: "stage" | "domain" = stage ? "stage" : "domain";
    const key = `${cause}|${whereKind}|${where}`;

    const existing = clusterAgg.get(key) ?? {
      key,
      cause,
      where,
      whereKind,
      count: 0,
      projectCount: 0,
      delayDays: 0,
      budgetCost: 0,
      qualityImpact: undefined,
      exampleTitle: lesson.title,
      recommendations: [],
      highSeverityCount: 0,
    };

    existing.count += 1;
    existing.delayDays += Math.max(0, lesson.impactScheduleDelay ?? 0);
    existing.budgetCost += Math.max(0, lesson.impactBudgetCost ?? 0);
    if (!existing.qualityImpact && lesson.impactQualityDesc?.trim()) {
      existing.qualityImpact = lesson.impactQualityDesc.trim();
    }
    if (lesson.risk === "high") existing.highSeverityCount += 1;
    const rec = lesson.recommendation?.trim();
    if (rec) existing.recommendations.push(rec);
    clusterAgg.set(key, existing);
  }
  // Distinct projects per cluster (impact must be visible across the organization).
  const projectsPerCluster = new Map<string, Set<number | string>>();
  for (const lesson of lessonsCreated) {
    const cause = lesson.category?.trim();
    const where = lesson.stage?.trim() || lesson.professionalDomain?.trim();
    if (!cause || !where) continue;
    const key = `${cause}|${lesson.stage?.trim() ? "stage" : "domain"}|${where}`;
    const set = projectsPerCluster.get(key) ?? new Set<number | string>();
    set.add(lesson.projectId ?? lesson.project ?? "unknown");
    projectsPerCluster.set(key, set);
  }
  const evidenceClusters = [...clusterAgg.values()]
    .map((c) => ({ ...c, projectCount: projectsPerCluster.get(c.key)?.size ?? 1 }))
    .sort((a, b) => b.count - a.count || b.delayDays - a.delayDays);


  // Zone C — problem domains: domains carrying high-severity lessons, with avg delay.
  const domainAgg = new Map<string, { count: number; delaySum: number; delayN: number }>();
  for (const l of lessonsCreated) {
    if (l.risk !== "high" || !l.professionalDomain) continue;
    const cur = domainAgg.get(l.professionalDomain) ?? { count: 0, delaySum: 0, delayN: 0 };
    cur.count += 1;
    const delay = l.impactScheduleDelay;
    if (typeof delay === "number" && delay > 0) { cur.delaySum += delay; cur.delayN += 1; }
    domainAgg.set(l.professionalDomain, cur);
  }
  const problemDomains: ProblemDomain[] = [...domainAgg.entries()]
    .map(([key, v]) => ({ key, count: v.count, avgDelayDays: v.delayN > 0 ? Math.round(v.delaySum / v.delayN) : null }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);

  // Delay (DelayDays = Actual - Planned, approximated by impactScheduleDelay).
  const delayed = lessonsCreated.filter((l) => typeof l.impactScheduleDelay === "number" && (l.impactScheduleDelay ?? 0) > 0);
  const delayedCount = delayed.length;
  const delayDaysTotal = delayed.reduce((s, l) => s + (l.impactScheduleDelay ?? 0), 0);
  const avgDelayDays = delayedCount > 0 ? Math.round(delayDaysTotal / delayedCount) : null;

  const highSeverityCount = lessonsCreated.filter((l) => l.risk === "high").length;

  const projectById = new Map(projects.map((p) => [p.id, p]));
  const lessonById = new Map(lessons.map((l) => [l.id, l]));
  const implCountByLesson = new Map<number, number>();
  for (const i of implsInPeriod) {
    if (i.isImplemented !== true) continue;
    implCountByLesson.set(i.lessonId, (implCountByLesson.get(i.lessonId) ?? 0) + 1);
  }
  let featuredLesson: FeaturedLesson | null = null;
  let bestCount = 0;
  let bestImpactScore = -1;
  for (const [lessonId, count] of implCountByLesson) {
    if (count < 2) continue;
    const l = lessonById.get(lessonId);
    if (!l) continue;
    const impactScore = Math.abs(l.impactScheduleDelay ?? 0) + Math.abs(l.impactBudgetCost ?? 0);
    if (count > bestCount || (count === bestCount && impactScore > bestImpactScore)) {
      bestCount = count;
      bestImpactScore = impactScore;
      featuredLesson = {
        id: l.id,
        title: l.title,
        projectName: l.projectId != null ? projectById.get(l.projectId)?.name : undefined,
        domain: l.professionalDomain,
        implementedCount: count,
      };
    }
  }

  const distributedDistinct = distinct(implsInPeriod.map((i) => i.lessonId)).length;
  const implementationRate = distributedDistinct > 0 ? kpiImplemented / distributedDistinct : null;

  const distributedDistinctPrev = distinct(implsPrev.map((i) => i.lessonId)).length;
  const implementedDistinctPrev = distinct(implsPrev.filter((i) => i.isImplemented === true).map((i) => i.lessonId)).length;
  const implementationRatePrev = distributedDistinctPrev > 0 ? implementedDistinctPrev / distributedDistinctPrev : null;
  const implementationRateDelta =
    implementationRate !== null && implementationRatePrev !== null
      ? (implementationRate - implementationRatePrev) * 100
      : null;

  const createdPrev = distinct(lessonsCreatedPrev.map((l) => l.id)).length;
  const createdDeltaPct = createdPrev > 0 ? ((kpiCreated - createdPrev) / createdPrev) * 100 : null;

  // Zone A — deterministic "what needs your attention" highlights.
  const attention: AttentionItem[] = [];
  if (highSeverityCount > 0) {
    attention.push({
      tone: "critical",
      label: `${highSeverityCount} לקחים בחומרה גבוהה`,
      detail: "מומלץ לוודא שננקטה פעולה מתקנת ושהלקחים הופצו לפרויקטים הרלוונטיים.",
    });
  }
  if (recurringPatterns.length > 0) {
    const top = recurringPatterns[0];
    attention.push({
      tone: "warning",
      label: `דפוס חוזר: ${top.key}`,
      detail: `הצירוף חזר ${top.count} פעמים בתקופה — כדאי לבדוק שורש בעיה משותף.`,
    });
  }
  if (delayedCount > 0) {
    attention.push({
      tone: "warning",
      label: `${delayedCount} לקחים עם עיכוב בלוח זמנים`,
      detail: `סה"כ ${delayDaysTotal} ימי עיכוב${avgDelayDays !== null ? `, בממוצע ${avgDelayDays} ימים ללקח` : ""}.`,
    });
  }
  if (createdPending > 0) {
    attention.push({
      tone: "info",
      label: `${createdPending} לקחים ממתינים לאישור`,
      detail: "אישור מהיר מזרז את ההפצה והיישום בשטח.",
    });
  }

  return {
    range,
    kpiCreated, createdApproved, createdPending, createdDraft,
    kpiApproved, kpiImplemented, kpiNotRelevant,
    activeUsersCount, totalUsersCount, activePct,
    attention,
    topPMs, topReferents, topImplementers,
    topCategories, topDomains, topStages,
    recurringPatterns, problemDomains, evidenceClusters,
    delayedCount, delayDaysTotal, avgDelayDays,
    featuredLesson,
    implementationRate, implementationRatePrev, implementationRateDelta,
    createdPrev, createdDeltaPct,
  };
};
