/**
 * "חסר נתונים" (PLAN-lessons-projects.md L4) — the ONE definition. The master
 * file owns name / stage / type / risk; what it cannot know (station type,
 * site, equipment) is the module's to complete. The PM sees it on their own
 * projects, the admin in the "השלמת נתונים" table; both read this. Pure, tested.
 */
import type { Project } from "@/context/UserContext";

export type ProjectProblem = "station_type_unset" | "no_site" | "no_equipment";

export const PROJECT_PROBLEM_LABELS: Record<ProjectProblem, string> = {
  station_type_unset: "סוג תחנה",
  no_site: "אתר",
  no_equipment: "ציוד",
};

export const projectProblems = (p: Pick<Project, "stationTypeSet" | "site" | "equipmentIds">): ProjectProblem[] => {
  const out: ProjectProblem[] = [];
  if (!p.stationTypeSet) out.push("station_type_unset");
  if (!p.site?.trim()) out.push("no_site");
  if (!p.equipmentIds?.length) out.push("no_equipment");
  return out;
};

export const missingLabel = (problems: ProjectProblem[]): string =>
  problems.length ? `חסר: ${problems.map((x) => PROJECT_PROBLEM_LABELS[x]).join(", ")}` : "";

/** Linked to the weekly master file: name / stage / type / risk / manager come from there. */
export const isLinked = (p: Pick<Project, "shanIdentifier">): boolean => Boolean(p.shanIdentifier);
