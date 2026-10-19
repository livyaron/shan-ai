/**
 * "חסר נתונים" (PLAN-lessons-projects.md L4) — the ONE definition. The master
 * file owns name / stage / type / risk; what it cannot know (station type,
 * site) is the module's to complete. The PM sees it on their own projects, the
 * admin in the "השלמת נתונים" table; both read this. Pure, tested.
 *
 * Equipment is NOT required (user decision 2026-10-19): it is still editable,
 * but an empty list never marks a project "חסר".
 */
import type { Project } from "@/context/UserContext";

export type ProjectProblem = "station_type_unset" | "no_site";

export const PROJECT_PROBLEM_LABELS: Record<ProjectProblem, string> = {
  station_type_unset: "סוג תחנה",
  no_site: "אתר",
};

export const projectProblems = (p: Pick<Project, "stationTypeSet" | "site">): ProjectProblem[] => {
  const out: ProjectProblem[] = [];
  if (!p.stationTypeSet) out.push("station_type_unset");
  if (!p.site?.trim()) out.push("no_site");
  return out;
};

export const missingLabel = (problems: ProjectProblem[]): string =>
  problems.length ? `חסר: ${problems.map((x) => PROJECT_PROBLEM_LABELS[x]).join(", ")}` : "";

/** Linked to the weekly master file: name / stage / type / risk / manager come from there. */
export const isLinked = (p: Pick<Project, "shanIdentifier">): boolean => Boolean(p.shanIdentifier);
