/**
 * קישור פרויקטים — the server decides the rules (app/services/lessons_project_link.py);
 * this file only names the codes and filters the list. Pure, tested.
 */

export interface MasterProject {
  identifier: string;
  name: string | null;
  manager: string | null;
  stage: string | null;
  project_type: string | null;
  linked_to?: number | null;
}

export interface Suggestion extends MasterProject {
  score: number;
}

export interface LinkRow {
  id: number;
  name: string;
  site: string | null;
  lessons_count: number;
  manager_name: string | null;
  identifier: string | null;
  linked: MasterProject | null;
  /** Linked to an identifier the file no longer holds as active. */
  linked_missing: boolean;
  decision: "local" | null;
  suggestions: Suggestion[];
}

export type ManagerProblem = "alias_unlinked" | "no_profile" | "not_pm";

export const MANAGER_PROBLEM_LABELS: Record<ManagerProblem, string> = {
  alias_unlinked: "השם בקובץ לא קושר למשתמש",
  no_profile: "למשתמש אין פרופיל במערכת הלקחים",
  not_pm: "המשתמש לא מוגדר כמנהל פרויקט",
};

export interface LinkSummary {
  lessons_total: number;
  linked: number;
  local: number;
  undecided: number;
  master_total: number;
  to_create: number;
  unknown_stage: { identifier: string; name: string | null; stage: string | null }[];
  managers_unlinked: { name: string; problem: ManagerProblem; projects: number }[];
  no_manager: number;
  type_unknown: string[];
}

export interface LinksPayload {
  projects: LinkRow[];
  masters: MasterProject[];
  summary: LinkSummary;
  can_create: boolean;
}

export type RowState = "linked" | "local" | "open";

export const rowState = (r: LinkRow): RowState => (r.identifier ? "linked" : r.decision === "local" ? "local" : "open");

/** Search (name / manager / linked file name) and "only open"; open rows first. */
export const filterLinkRows = (rows: LinkRow[], query: string, onlyOpen: boolean): LinkRow[] => {
  const q = query.trim().toLowerCase();
  return rows
    .filter((r) => !onlyOpen || rowState(r) === "open")
    .filter((r) => !q || [r.name, r.manager_name ?? "", r.linked?.name ?? "", r.identifier ?? ""]
      .some((s) => s.toLowerCase().includes(q)))
    .sort((a, b) => Number(rowState(b) === "open") - Number(rowState(a) === "open"));
};

/** File projects still free to link (not linked to another lessons project). */
export const freeMasters = (masters: MasterProject[], forRow: number): MasterProject[] =>
  masters.filter((m) => m.linked_to == null || m.linked_to === forRow);

export const masterLabel = (m: MasterProject): string =>
  [m.name || m.identifier, m.manager ? `מנה"פ ${m.manager}` : "", m.stage ?? ""].filter(Boolean).join(" · ");
