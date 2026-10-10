import type { LessonImplementation } from "@/context/UserContext";

export interface LessonQualityMetrics {
  lessonId: number;
  distributedCount: number;
  respondedCount: number;
  implementedCount: number;
  relevantNotImplementedCount: number;
  notRelevantCount: number;
  totalScore: number;
  avgScore: number | null;
  implementationRate: number | null;
  qualityScoreShrunk: number; // for AI ranking, shrinkage applied
}

const SHRINK_K = 3;
const SHRINK_PRIOR = 1.0;

/**
 * Score per response:
 *   relevant + implemented => 2
 *   relevant + not implemented => 1
 *   not relevant => 0
 * Only responses (responded_at not null, modeled here as having isRelevant defined) count toward avg/totals.
 */
export function scoreImplementation(impl: LessonImplementation): number | null {
  if (impl.isRelevant === undefined || impl.isRelevant === null) return null;
  if (impl.isRelevant === false) return 0;
  return impl.isImplemented ? 2 : 1;
}

export function computeLessonQuality(
  lessonId: number,
  impls: LessonImplementation[],
): LessonQualityMetrics {
  const lessonImpls = impls.filter((i) => i.lessonId === lessonId);
  const distributedCount = lessonImpls.length;

  let respondedCount = 0;
  let implementedCount = 0;
  let relevantNotImplementedCount = 0;
  let notRelevantCount = 0;
  let totalScore = 0;

  for (const i of lessonImpls) {
    const s = scoreImplementation(i);
    if (s === null) continue;
    respondedCount++;
    totalScore += s;
    if (i.isRelevant === false) notRelevantCount++;
    else if (i.isImplemented) implementedCount++;
    else relevantNotImplementedCount++;
  }

  const avgScore = respondedCount > 0 ? totalScore / respondedCount : null;
  const implementationRate =
    respondedCount > 0 ? implementedCount / respondedCount : null;

  // Bayesian shrinkage toward neutral prior (1.0 on a 0..2 scale).
  const qualityScoreShrunk =
    ((avgScore ?? SHRINK_PRIOR) * respondedCount + SHRINK_PRIOR * SHRINK_K) /
    (respondedCount + SHRINK_K);

  return {
    lessonId,
    distributedCount,
    respondedCount,
    implementedCount,
    relevantNotImplementedCount,
    notRelevantCount,
    totalScore,
    avgScore,
    implementationRate,
    qualityScoreShrunk,
  };
}

export function computeAllLessonQuality(
  lessonIds: number[],
  impls: LessonImplementation[],
): Map<number, LessonQualityMetrics> {
  const map = new Map<number, LessonQualityMetrics>();
  for (const id of lessonIds) map.set(id, computeLessonQuality(id, impls));
  return map;
}
