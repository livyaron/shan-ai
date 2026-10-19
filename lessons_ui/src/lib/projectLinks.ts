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
  /** The lessons project already linked to this file project, if any. */
  linked_to?: number | null;
  linked_name?: string | null;
}

export interface Suggestion extends MasterProject {
  /** Name similarity 0–100 (100 when the file identifier is written in the name). */
  score: number;
  id_match: boolean;
  manager_match: boolean;
  /** At most the first suggestion: the server's rule found a clear signal. */
  recommended: boolean;
}

/** The suggestion picked before the admin chooses: the recommended one, else none. */
export const defaultPick = (r: LinkRow): string => r.suggestions.find((s) => s.recommended)?.identifier ?? "";

export type ManagerStatus = "match" | "differs" | "unresolved" | "kept" | "no_name";

export interface ManagerCheck {
  status: ManagerStatus;
  file_manager: string | null;
  problem: ManagerProblem | "no_name" | null;
  file_profile: { id: string; name: string | null } | null;
  current: { id: string; name: string | null };
  suggest_user_id: number | null;
}

/** A linked project whose manager needs the admin's answer. */
export const managerNeedsAnswer = (r: LinkRow): boolean =>
  r.manager_check?.status === "differs" || r.manager_check?.status === "unresolved";

/** What still needs the admin on one lessons project — the server's link_issues (the ONE definition). */
export type LinkIssue = "undecided" | "manager_differs" | "manager_unresolved" | "manager_placeholder" | "sync_error";

export const ISSUE_LABELS: Record<LinkIssue, string> = {
  undecided: "לא הוחלט קישור",
  manager_differs: "מנה״פ שונה מהקובץ — ממתין לאישור",
  manager_unresolved: "מנה״פ הקובץ לא מזוהה",
  manager_placeholder: "מנה״פ \"טרם שויך\"",
  sync_error: "שגיאת סנכרון",
};

export interface LinkRow {
  manager_check: ManagerCheck | null;
  id: number;
  name: string;
  site: string | null;
  lessons_count: number;
  manager_name: string | null;
  manager_id?: string | null;
  identifier: string | null;
  linked: MasterProject | null;
  /** Linked to an identifier the file no longer holds as active. */
  linked_missing: boolean;
  decision: "local" | null;
  suggestions: Suggestion[];
  /** Empty = handled: the row leaves the work list (2026-10-19). */
  issues?: LinkIssue[];
  sync_error?: string | null;
  sync_error_at?: string | null;
}

export type ManagerProblem = "alias_unlinked" | "no_profile" | "not_pm";

export const MANAGER_PROBLEM_LABELS: Record<ManagerProblem, string> = {
  alias_unlinked: "השם בקובץ לא קושר למשתמש",
  no_profile: "למשתמש אין פרופיל במערכת הלקחים",
  not_pm: "המשתמש לא מוגדר כמנהל פרויקט",
};

export type MissingField = "stage" | "project_type";

/** A file project that waits for the admin: the file does not give a required field. */
export interface HeldProject {
  identifier: string;
  name: string | null;
  manager: string | null;
  stage: string | null;
  project_type: string | null;
  missing: MissingField[];
}

export const MISSING_LABELS: Record<MissingField, string> = { stage: "שלב", project_type: "סוג פרויקט" };

export interface LinkSummary {
  lessons_total: number;
  linked: number;
  local: number;
  undecided: number;
  master_total: number;
  to_create: number;
  held: HeldProject[];
  managers_unlinked: { name: string; problem: ManagerProblem; projects: number }[];
  managers_open: number;
  no_manager: number;
  pending: number;
  handled: number;
  engine_ok: boolean;
  all_clear: boolean;
}

/** One sync run, in the five categories asked for (2026-10-19). */
export interface SyncReport {
  at: string | null;
  actor: string | null;
  created: { identifier: string; name: string | null }[];
  linked_ok: number;
  awaiting_data: { identifier: string; name: string | null; missing: MissingField[] }[];
  awaiting_manager: number;
  failed: { id?: number; identifier?: string; name?: string | null; error: string }[];
  updated: number;
  managers_filled: number;
  inactive: number;
  creation_paused: number;
  engine_ok: boolean;
}

export interface LinksPayload {
  projects: LinkRow[];
  masters: MasterProject[];
  summary: LinkSummary;
  can_create: boolean;
  users: { id: number; username: string }[];
  stage_options: string[];
  type_options: { value: string; label: string }[];
  last_sync: SyncReport | null;
}

export type LinkView = "pending" | "handled";

/** A row is pending while it has an open issue; the server decides, this only reads it. */
export const isPending = (r: LinkRow): boolean => (r.issues ?? []).length > 0;

export type RowState = "linked" | "local" | "open";

export const rowState = (r: LinkRow): RowState => (r.identifier ? "linked" : r.decision === "local" ? "local" : "open");

/** Search (name / manager / linked file name) within one view: the work list
 * ("pending", the default) or the handled projects ("handled", for control).
 * A handled project never shows in the work list. Undecided rows first. */
export const filterLinkRows = (rows: LinkRow[], query: string, view: LinkView): LinkRow[] => {
  const q = query.trim().toLowerCase();
  return rows
    .filter((r) => (view === "pending" ? isPending(r) : !isPending(r)))
    .filter((r) => !q || [r.name, r.manager_name ?? "", r.linked?.name ?? "", r.identifier ?? ""]
      .some((s) => s.toLowerCase().includes(q)))
    .sort((a, b) => Number(rowState(b) === "open") - Number(rowState(a) === "open"));
};

/** The file's manager names: the work list shows only the ones still to link. */
export const visibleManagers = (rows: ManagerRow[], view: LinkView): ManagerRow[] =>
  rows.filter((m) => (view === "pending" ? m.problem !== null : m.problem === null));

/** A file project another lessons project already holds: the answer is a merge, not a link. */
export const heldElsewhere = (m: MasterProject, forRow: number): boolean => m.linked_to != null && m.linked_to !== forRow;

/** The manual list: every active file project, the ones held elsewhere marked. */
export const manualOptions = (masters: MasterProject[], forRow: number) =>
  masters.map((m) => ({
    value: m.identifier,
    label: heldElsewhere(m, forRow) ? `${masterLabel(m)} · מקושר ל"${(m.linked_name ?? "").trim()}" — בחירה תציע איחוד` : masterLabel(m),
  }));

export const masterLabel = (m: MasterProject): string =>
  [m.name || m.identifier, m.manager ? `מנה"פ ${m.manager}` : "", m.stage ?? ""].filter(Boolean).join(" · ");

export type SearchReason = "free" | "linked" | "inactive";

export interface SearchRow {
  identifier: string;
  name: string | null;
  manager: string | null;
  stage: string | null;
  is_active: boolean;
  linked_to: number | null;
  linked_name: string | null;
  score: number;
  reason: SearchReason;
}

export const SEARCH_REASON_LABELS: Record<SearchReason, string> = {
  free: "פעיל ופנוי — אפשר לקשר",
  linked: "כבר מקושר לפרויקט לקחים",
  inactive: "לא פעיל — לא נמצא בגיליון \"דוח שבועי עדכני\" בהעלאה האחרונה",
};

export interface ManagerRow {
  name: string;
  projects: number;
  user_id: number | null;
  username: string | null;
  profile_id: string | null;
  problem: ManagerProblem | null;
  hint_user_id: number | null;
}

export interface ManagersPayload {
  rows: ManagerRow[];
  users: { id: number; username: string }[];
  open: number;
}
