/**
 * Admin dashboard analytics — deterministic pattern detection.
 *
 * All thresholds are intentional starting values, tunable in one place.
 * Every result includes the lessonIds[] that compose it, so the UI can drill
 * down to a real filtered list (Explainable Insights).
 *
 * Scope: admin-only. Pure functions — no React, no I/O.
 */
import type { Lesson, LessonImplementation, ReferentReview, Project, MockUser } from "@/context/UserContext";

// ─────────────── Thresholds ───────────────
export const ADMIN_THRESHOLDS = {
  // "Repeating" pattern: appears across N distinct projects with M total lessons
  REPEATING_MIN_PROJECTS: 3,
  REPEATING_MIN_LESSONS: 5,
  REPEATING_WINDOW_DAYS: 90,

  // "Trend": ±X% change between last 30 days vs 30 before that, with floor
  TREND_PCT_DELTA: 0.5, // 50%
  TREND_MIN_RECENT: 5,

  // "Anomaly"
  NOT_RELEVANT_PCT: 0.4, // 40%
  NOT_RELEVANT_MIN_RESPONSES: 10,
  REFERENT_OVERLOAD_FACTOR: 1.5, // × median
  REFERENT_MIN_LESSONS: 5,
  SLA_OVERDUE_PCT: 0.25, // 25%
  SLA_MIN_ACTIVE: 4,

  // "Risk hotspot"
  IMPL_LOW_PCT: 0.25, // < 25% implemented
  IMPL_MIN_DISTRIBUTED: 5,
  GAP_MIN_CREATED_6M: 10,
  GAP_IMPL_PCT: 0.2, // < 20%

  // Misc
  SLA_DAYS: 10,
  MONTHS_DEFAULT: 6,
} as const;

export type InsightKind = "repeating" | "trend" | "anomaly" | "risk";

export interface AnalyticsInsight {
  id: string;
  kind: InsightKind;
  severity: 1 | 2 | 3 | 4; // 4 = highest (risk) → 1 (repeating)
  title: string;          // short Hebrew label (AI-phraseable)
  evidence: string;       // factual phrasing of the numbers
  contextType: "category" | "domain" | "stage" | "referent" | "project_manager";
  contextValue: string;
  lessonIds: number[];
  metric: number;         // primary number (count or %)
}

// ─────────────── Helpers ───────────────
const parseDate = (s?: string | null): Date | null => {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
};

const daysAgo = (n: number) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
};

const distinct = <T>(xs: T[]) => [...new Set(xs)];

const monthKey = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;

// ─────────────── Distinct-lesson KPI counters ───────────────
export const distinctImplementedLessonIds = (impls: LessonImplementation[]): number[] =>
  distinct(impls.filter((i) => i.isImplemented === true).map((i) => i.lessonId));

export const distinctNotRelevantLessonIds = (impls: LessonImplementation[]): number[] =>
  distinct(impls.filter((i) => i.isRelevant === false).map((i) => i.lessonId));

// ─────────────── Monthly trends (4 series) ───────────────
export interface MonthlyTrendPoint {
  name: string;       // Hebrew month label
  monthKey: string;
  created: number;
  approved: number;
  implemented: number;
  notRelevant: number;
  createdIds: number[];
  approvedIds: number[];
  implementedIds: number[];
  notRelevantIds: number[];
}

const HE_MONTHS = ["ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"];

export const computeMonthlyTrends = (
  lessons: Lesson[],
  impls: LessonImplementation[],
  months: number = ADMIN_THRESHOLDS.MONTHS_DEFAULT,
): MonthlyTrendPoint[] => {
  const now = new Date();
  const points: MonthlyTrendPoint[] = [];
  for (let i = months - 1; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    points.push({
      name: HE_MONTHS[d.getMonth()],
      monthKey: monthKey(d),
      created: 0, approved: 0, implemented: 0, notRelevant: 0,
      createdIds: [], approvedIds: [], implementedIds: [], notRelevantIds: [],
    });
  }
  const idx = new Map(points.map((p, i) => [p.monthKey, i]));

  for (const l of lessons) {
    const created = parseDate(l.date);
    if (created) {
      const k = monthKey(created);
      const p = idx.get(k);
      if (p !== undefined) {
        points[p].createdIds.push(l.id);
      }
    }
    if (l.workflowStatus === "אושר והופץ") {
      const approved = parseDate(l.closedDate) || parseDate(l.updatedAt);
      if (approved) {
        const p = idx.get(monthKey(approved));
        if (p !== undefined) points[p].approvedIds.push(l.id);
      }
    }
  }

  // Implementations & not-relevant by responded_at (distinct per lesson per month)
  const monthImpl = new Map<string, Set<number>>();
  const monthNR = new Map<string, Set<number>>();
  for (const imp of impls) {
    const d = parseDate(imp.respondedAt);
    if (!d) continue;
    const k = monthKey(d);
    if (!idx.has(k)) continue;
    if (imp.isImplemented === true) {
      if (!monthImpl.has(k)) monthImpl.set(k, new Set());
      monthImpl.get(k)!.add(imp.lessonId);
    }
    if (imp.isRelevant === false) {
      if (!monthNR.has(k)) monthNR.set(k, new Set());
      monthNR.get(k)!.add(imp.lessonId);
    }
  }

  for (const p of points) {
    p.createdIds = distinct(p.createdIds);
    p.approvedIds = distinct(p.approvedIds);
    p.implementedIds = [...(monthImpl.get(p.monthKey) ?? [])];
    p.notRelevantIds = [...(monthNR.get(p.monthKey) ?? [])];
    p.created = p.createdIds.length;
    p.approved = p.approvedIds.length;
    p.implemented = p.implementedIds.length;
    p.notRelevant = p.notRelevantIds.length;
  }
  return points;
};

// ─────────────── Repeating topics ───────────────
export type RepeatingDimension = "category" | "domain" | "stage";

export interface RepeatingItem {
  key: string;
  projectsCount: number;
  lessonsCount: number;
  projectIds: number[];
  lessonIds: number[];
}

export const computeRepeating = (
  lessons: Lesson[],
  dimension: RepeatingDimension,
  windowDays: number = ADMIN_THRESHOLDS.REPEATING_WINDOW_DAYS,
): RepeatingItem[] => {
  const since = daysAgo(windowDays);
  const map = new Map<string, { projectIds: Set<number>; lessonIds: number[] }>();
  for (const l of lessons) {
    const d = parseDate(l.date);
    if (d && d < since) continue;
    let key: string | undefined;
    if (dimension === "category") key = l.category;
    else if (dimension === "domain") key = l.professionalDomain;
    else if (dimension === "stage") key = l.stage;
    if (!key) continue;
    if (!map.has(key)) map.set(key, { projectIds: new Set(), lessonIds: [] });
    const e = map.get(key)!;
    if (l.projectId != null) e.projectIds.add(l.projectId);
    e.lessonIds.push(l.id);
  }
  return [...map.entries()]
    .map(([key, v]) => ({
      key,
      projectsCount: v.projectIds.size,
      lessonsCount: v.lessonIds.length,
      projectIds: [...v.projectIds],
      lessonIds: v.lessonIds,
    }))
    .sort((a, b) => b.projectsCount - a.projectsCount || b.lessonsCount - a.lessonsCount);
};

// ─────────────── Insights (all four kinds) ───────────────
export const findInsights = (
  lessons: Lesson[],
  impls: LessonImplementation[],
  _reviews: ReferentReview[],
  projects: Project[],
  users: MockUser[],
): AnalyticsInsight[] => {
  const out: AnalyticsInsight[] = [];

  // ── REPEATING ── (across distinct projects)
  for (const dim of ["category", "domain", "stage"] as RepeatingDimension[]) {
    const items = computeRepeating(lessons, dim);
    for (const it of items) {
      if (
        it.projectsCount >= ADMIN_THRESHOLDS.REPEATING_MIN_PROJECTS &&
        it.lessonsCount >= ADMIN_THRESHOLDS.REPEATING_MIN_LESSONS
      ) {
        out.push({
          id: `rep-${dim}-${it.key}`,
          kind: "repeating",
          severity: 1,
          title: `${dim === "category" ? "קטגוריה" : dim === "domain" ? "תחום" : "שלב"} חוזר: ${it.key}`,
          evidence: `מופיע ב-${it.projectsCount} פרויקטים שונים, סה"כ ${it.lessonsCount} לקחים ב-${ADMIN_THRESHOLDS.REPEATING_WINDOW_DAYS} הימים האחרונים`,
          contextType: dim,
          contextValue: it.key,
          lessonIds: it.lessonIds,
          metric: it.projectsCount,
        });
      }
    }
  }

  // ── TREND ── (category & domain, last 30 vs prev 30)
  const t30 = daysAgo(30);
  const t60 = daysAgo(60);
  const bucket = (getKey: (l: Lesson) => string | undefined) => {
    const recent = new Map<string, number[]>();
    const prev = new Map<string, number[]>();
    for (const l of lessons) {
      const d = parseDate(l.date);
      if (!d) continue;
      const k = getKey(l);
      if (!k) continue;
      if (d >= t30) {
        if (!recent.has(k)) recent.set(k, []);
        recent.get(k)!.push(l.id);
      } else if (d >= t60) {
        if (!prev.has(k)) prev.set(k, []);
        prev.get(k)!.push(l.id);
      }
    }
    return { recent, prev };
  };
  const trendDims: { dim: "category" | "domain"; getKey: (l: Lesson) => string | undefined }[] = [
    { dim: "category", getKey: (l) => l.category },
    { dim: "domain",   getKey: (l) => l.professionalDomain },
  ];
  for (const { dim, getKey } of trendDims) {
    const { recent, prev } = bucket(getKey);
    for (const [key, ids] of recent) {
      if (ids.length < ADMIN_THRESHOLDS.TREND_MIN_RECENT) continue;
      const prevCount = (prev.get(key) ?? []).length;
      if (prevCount === 0) continue;
      const delta = (ids.length - prevCount) / prevCount;
      if (Math.abs(delta) < ADMIN_THRESHOLDS.TREND_PCT_DELTA) continue;
      const dir = delta > 0 ? "עלייה" : "ירידה";
      out.push({
        id: `trend-${dim}-${key}`,
        kind: "trend",
        severity: 2,
        title: `${dir} ב${dim === "category" ? "קטגוריה" : "תחום"}: ${key}`,
        evidence: `${dir} של ${Math.round(Math.abs(delta) * 100)}% ב-30 הימים האחרונים (${ids.length} לקחים, מול ${prevCount} ב-30 הימים שלפניהם)`,
        contextType: dim,
        contextValue: key,
        lessonIds: ids,
        metric: Math.round(delta * 100),
      });
    }
  }

  // ── ANOMALY: high "not relevant" rate per domain ──
  const domainResp = new Map<string, { responses: number; notRelevant: number; lessonIds: Set<number> }>();
  const lessonById = new Map(lessons.map((l) => [l.id, l]));
  for (const imp of impls) {
    const l = lessonById.get(imp.lessonId);
    const dom = l?.professionalDomain;
    if (!dom) continue;
    if (imp.isRelevant === undefined || imp.isRelevant === null) continue;
    if (!domainResp.has(dom)) domainResp.set(dom, { responses: 0, notRelevant: 0, lessonIds: new Set() });
    const e = domainResp.get(dom)!;
    e.responses++;
    e.lessonIds.add(imp.lessonId);
    if (imp.isRelevant === false) e.notRelevant++;
  }
  for (const [dom, v] of domainResp) {
    if (v.responses < ADMIN_THRESHOLDS.NOT_RELEVANT_MIN_RESPONSES) continue;
    const pct = v.notRelevant / v.responses;
    if (pct < ADMIN_THRESHOLDS.NOT_RELEVANT_PCT) continue;
    out.push({
      id: `anom-nr-${dom}`,
      kind: "anomaly",
      severity: 3,
      title: `שיעור "לא רלוונטי" חריג בתחום ${dom}`,
      evidence: `${Math.round(pct * 100)}% מהתגובות סומנו כלא רלוונטיות (${v.notRelevant} מתוך ${v.responses})`,
      contextType: "domain",
      contextValue: dom,
      lessonIds: [...v.lessonIds],
      metric: Math.round(pct * 100),
    });
  }

  // ── ANOMALY: referent overload ──
  const refLoad = new Map<string, number[]>();
  for (const l of lessons) {
    if (l.workflowStatus !== "בטיפול רפרנט") continue;
    for (const rid of l.distributedToReferents ?? []) {
      if (!refLoad.has(rid)) refLoad.set(rid, []);
      refLoad.get(rid)!.push(l.id);
    }
  }
  const counts = [...refLoad.values()].map((a) => a.length).sort((a, b) => a - b);
  const median = counts.length ? counts[Math.floor(counts.length / 2)] : 0;
  for (const [rid, ids] of refLoad) {
    if (ids.length < ADMIN_THRESHOLDS.REFERENT_MIN_LESSONS) continue;
    if (median <= 0 || ids.length < median * ADMIN_THRESHOLDS.REFERENT_OVERLOAD_FACTOR) continue;
    const u = users.find((x) => x.id === rid);
    out.push({
      id: `anom-ref-${rid}`,
      kind: "anomaly",
      severity: 3,
      title: `עומס חריג על הרפרנט ${u?.name ?? rid}`,
      evidence: `${ids.length} לקחים בטיפול (חציון: ${median})`,
      contextType: "referent",
      contextValue: u?.name ?? rid,
      lessonIds: ids,
      metric: ids.length,
    });
  }

  // ── ANOMALY: SLA overdue % by stage ──
  const stageSla = new Map<string, { active: number[]; overdue: number[] }>();
  for (const l of lessons) {
    if (["אושר והופץ", "נדחה"].includes(l.workflowStatus)) continue;
    if (!l.stage) continue;
    if (!stageSla.has(l.stage)) stageSla.set(l.stage, { active: [], overdue: [] });
    stageSla.get(l.stage)!.active.push(l.id);
    const start = parseDate(l.referentStartDate);
    if (start && (Date.now() - start.getTime()) / 86400000 > ADMIN_THRESHOLDS.SLA_DAYS) {
      stageSla.get(l.stage)!.overdue.push(l.id);
    }
  }
  for (const [stage, v] of stageSla) {
    if (v.active.length < ADMIN_THRESHOLDS.SLA_MIN_ACTIVE) continue;
    const pct = v.overdue.length / v.active.length;
    if (pct < ADMIN_THRESHOLDS.SLA_OVERDUE_PCT) continue;
    out.push({
      id: `anom-sla-${stage}`,
      kind: "anomaly",
      severity: 3,
      title: `איחור SLA חריג בשלב ${stage}`,
      evidence: `${Math.round(pct * 100)}% מהלקחים בשלב באיחור (${v.overdue.length} מתוך ${v.active.length})`,
      contextType: "stage",
      contextValue: stage,
      lessonIds: v.overdue,
      metric: Math.round(pct * 100),
    });
  }

  // ── RISK: low implementation rate per stage/domain ──
  const aggregateImpl = (getKey: (l: Lesson) => string | undefined, ctx: "stage" | "domain") => {
    const m = new Map<string, { distributed: Set<number>; implemented: Set<number>; responses: number }>();
    for (const imp of impls) {
      const l = lessonById.get(imp.lessonId);
      const k = l && getKey(l);
      if (!k) continue;
      if (!m.has(k)) m.set(k, { distributed: new Set(), implemented: new Set(), responses: 0 });
      const e = m.get(k)!;
      e.distributed.add(imp.lessonId);
      if (imp.isImplemented !== undefined && imp.isImplemented !== null) e.responses++;
      if (imp.isImplemented === true) e.implemented.add(imp.lessonId);
    }
    for (const [k, v] of m) {
      if (v.distributed.size < ADMIN_THRESHOLDS.IMPL_MIN_DISTRIBUTED) continue;
      if (v.responses === 0) continue;
      const pct = v.implemented.size / v.distributed.size;
      if (pct >= ADMIN_THRESHOLDS.IMPL_LOW_PCT) continue;
      out.push({
        id: `risk-impl-${ctx}-${k}`,
        kind: "risk",
        severity: 4,
        title: `יישום נמוך ב${ctx === "stage" ? "שלב" : "תחום"} ${k}`,
        evidence: `רק ${Math.round(pct * 100)}% מהלקחים שהופצו יושמו (${v.implemented.size} מתוך ${v.distributed.size})`,
        contextType: ctx,
        contextValue: k,
        lessonIds: [...v.distributed],
        metric: Math.round(pct * 100),
      });
    }
  };
  aggregateImpl((l) => l.stage, "stage");
  aggregateImpl((l) => l.professionalDomain, "domain");

  // ── RISK: gap created vs implemented (last 6 months, by domain) ──
  const since6m = daysAgo(180);
  const gapByDomain = new Map<string, { created: Set<number>; implemented: Set<number> }>();
  for (const l of lessons) {
    const dom = l.professionalDomain;
    if (!dom) continue;
    const d = parseDate(l.date);
    if (!d || d < since6m) continue;
    if (!gapByDomain.has(dom)) gapByDomain.set(dom, { created: new Set(), implemented: new Set() });
    gapByDomain.get(dom)!.created.add(l.id);
  }
  for (const imp of impls) {
    if (imp.isImplemented !== true) continue;
    const l = lessonById.get(imp.lessonId);
    const dom = l?.professionalDomain;
    if (!dom) continue;
    if (gapByDomain.has(dom) && gapByDomain.get(dom)!.created.has(imp.lessonId)) {
      gapByDomain.get(dom)!.implemented.add(imp.lessonId);
    }
  }
  for (const [dom, v] of gapByDomain) {
    if (v.created.size < ADMIN_THRESHOLDS.GAP_MIN_CREATED_6M) continue;
    const pct = v.implemented.size / v.created.size;
    if (pct >= ADMIN_THRESHOLDS.GAP_IMPL_PCT) continue;
    out.push({
      id: `risk-gap-${dom}`,
      kind: "risk",
      severity: 4,
      title: `פער יצירה-יישום בתחום ${dom}`,
      evidence: `${v.created.size} לקחים נוצרו ב-6 החודשים האחרונים, רק ${v.implemented.size} יושמו (${Math.round(pct * 100)}%)`,
      contextType: "domain",
      contextValue: dom,
      lessonIds: [...v.created],
      metric: Math.round(pct * 100),
    });
  }

  // Sort by severity desc, then by metric desc; cap at 8
  return out
    .sort((a, b) => b.severity - a.severity || b.metric - a.metric)
    .slice(0, 8);
};

// ─────────────── Bar data: responsible parties ───────────────
export interface PartyBar {
  id: string;
  name: string;
  fullName: string;
  לקחים: number;
  lessonIds: number[];
}

export const computeByReferent = (
  lessons: Lesson[],
  users: MockUser[],
): PartyBar[] => {
  const map = new Map<string, number[]>();
  for (const l of lessons) {
    if (l.workflowStatus !== "בטיפול רפרנט") continue;
    for (const rid of l.distributedToReferents ?? []) {
      if (!map.has(rid)) map.set(rid, []);
      map.get(rid)!.push(l.id);
    }
  }
  return [...map.entries()]
    .map(([rid, ids]) => {
      const u = users.find((x) => x.id === rid);
      const fullName = u?.name ?? rid;
      return {
        id: rid,
        name: fullName.length > 14 ? fullName.slice(0, 14) + "…" : fullName,
        fullName,
        לקחים: ids.length,
        lessonIds: ids,
      };
    })
    .sort((a, b) => b.לקחים - a.לקחים)
    .slice(0, 10);
};

export const computeByProjectManager = (
  lessons: Lesson[],
  projects: Project[],
  users: MockUser[],
): PartyBar[] => {
  const projectsById = new Map(projects.map((p) => [p.id, p]));
  const map = new Map<string, number[]>();
  for (const l of lessons) {
    if (l.projectId == null) continue;
    const p = projectsById.get(l.projectId);
    if (!p) continue;
    if (!map.has(p.managerId)) map.set(p.managerId, []);
    map.get(p.managerId)!.push(l.id);
  }
  return [...map.entries()]
    .map(([mid, ids]) => {
      const u = users.find((x) => x.id === mid);
      const fullName = u?.name ?? mid;
      return {
        id: mid,
        name: fullName.length > 14 ? fullName.slice(0, 14) + "…" : fullName,
        fullName,
        לקחים: ids.length,
        lessonIds: ids,
      };
    })
    .sort((a, b) => b.לקחים - a.לקחים)
    .slice(0, 10);
};

// ─────────────── Quality & adoption ───────────────
export interface DomainQuality {
  domain: string;
  created: number;
  implemented: number;
  createdIds: number[];
  implementedIds: number[];
  implPct: number;
}

export const computeDomainQuality = (
  lessons: Lesson[],
  impls: LessonImplementation[],
): DomainQuality[] => {
  const lessonById = new Map(lessons.map((l) => [l.id, l]));
  const implByDomain = new Map<string, Set<number>>();
  for (const imp of impls) {
    if (imp.isImplemented !== true) continue;
    const dom = lessonById.get(imp.lessonId)?.professionalDomain;
    if (!dom) continue;
    if (!implByDomain.has(dom)) implByDomain.set(dom, new Set());
    implByDomain.get(dom)!.add(imp.lessonId);
  }
  const createdByDomain = new Map<string, number[]>();
  for (const l of lessons) {
    const dom = l.professionalDomain;
    if (!dom) continue;
    if (!createdByDomain.has(dom)) createdByDomain.set(dom, []);
    createdByDomain.get(dom)!.push(l.id);
  }
  const out: DomainQuality[] = [];
  for (const [dom, createdIds] of createdByDomain) {
    const implIds = [...(implByDomain.get(dom) ?? [])];
    out.push({
      domain: dom,
      created: createdIds.length,
      implemented: implIds.length,
      createdIds,
      implementedIds: implIds,
      implPct: createdIds.length ? implIds.length / createdIds.length : 0,
    });
  }
  return out.sort((a, b) => b.created - a.created);
};
