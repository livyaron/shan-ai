import { describe, expect, it } from "vitest";
import { filterPeople, isGroupAccount, type PersonRow } from "@/lib/peopleSetup";

const person = (id: number, username: string, problems: PersonRow["problems"] = [], role = "project_manager"): PersonRow => ({
  id, username, job_title: null, email: `${id}@x.il`, groups: [], problems,
  profile: { id: `s${id}`, name: username, role, assigned_projects: [], source: "local" },
});

describe("filterPeople", () => {
  const rows = [person(1, "דנה"), person(2, "לירון גולן", ["unset"]), person(3, "אבי", ["pm_no_projects"])];

  it("puts the people who are not set up first", () => {
    expect(filterPeople(rows, "", false).map((p) => p.id)).toEqual([2, 3, 1]);
  });

  it("keeps only the not-set-up when asked", () => {
    expect(filterPeople(rows, "", true).map((p) => p.id)).toEqual([2, 3]);
  });

  it("searches name and e-mail", () => {
    expect(filterPeople(rows, "גולן", false).map((p) => p.id)).toEqual([2]);
    expect(filterPeople(rows, "3@x", false).map((p) => p.id)).toEqual([3]);
  });
});

it("a referent group's login account is not a person", () => {
  expect(isGroupAccount(person(9, "רפרנט מגזר", [], "referent"))).toBe(true);
  expect(isGroupAccount(person(9, "דנה"))).toBe(false);
});
