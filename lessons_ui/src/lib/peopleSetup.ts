/**
 * ניהול משתמשים — the server decides who is "not set up" (lessons_admin.person_problems /
 * group_problems); this file only names the codes and filters the list. Pure, tested.
 */

export type PersonProblem = "unset" | "pm_no_projects";
export type GroupProblem = "no_members" | "no_stages";

export const PERSON_PROBLEM_LABELS: Record<PersonProblem, string> = {
  unset: "לא הוגדר — ייכנס כצופה",
  pm_no_projects: "מנהל פרויקט בלי פרויקטים",
};

export const GROUP_PROBLEM_LABELS: Record<GroupProblem, string> = {
  no_members: "אין חברים בקבוצה",
  no_stages: "לא הוגדרו שלבים",
};

export interface PersonRow {
  id: number;
  username: string;
  job_title: string | null;
  email: string;
  profile: {
    id: string;
    name: string;
    role: string;
    assigned_projects: number[];
    source: "local" | "imported";
  } | null;
  groups: string[];
  problems: PersonProblem[];
}

export interface GroupRow {
  id: string;
  name: string;
  assigned_stage_indexes: number[];
  members: { id: number; username: string }[];
  login_username: string | null;
  problems: GroupProblem[];
}

export interface PeoplePayload {
  people: PersonRow[];
  groups: GroupRow[];
  migration_left: number;
}

/** Search (name / job title / e-mail) and "only not set up"; not-set-up first. */
export const filterPeople = (people: PersonRow[], query: string, onlyProblems: boolean): PersonRow[] => {
  const q = query.trim().toLowerCase();
  return people
    .filter((p) => !onlyProblems || p.problems.length > 0)
    .filter((p) => !q || [p.username, p.job_title ?? "", p.email].some((s) => s.toLowerCase().includes(q)))
    .sort((a, b) => Number(b.problems.length > 0) - Number(a.problems.length > 0));
};

/** A group's own login account is set up through the group, never as a person. */
export const isGroupAccount = (p: PersonRow): boolean => p.profile?.role === "referent";
