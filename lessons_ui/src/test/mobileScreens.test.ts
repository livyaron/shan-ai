import { afterEach, describe, expect, it, vi } from "vitest";
import { canToggleScope, myScope, scopedLessons } from "@/lib/lessonScope";
import { opensPhoneHome, prefersDesktop, setPrefersDesktop, showsTabBar } from "@/lib/standalone";
import { isImageUrl } from "@/lib/lessonFiles";

const lessons = [
  { id: 1, createdBy: "pm" },
  { id: 2, createdBy: "other", distributedToReferents: ["r1"] },
  { id: 3, createdBy: "r1" },
  { id: 4, createdBy: "other" },
];
const ids = (ls: { id: number }[]) => ls.map((l) => l.id);

describe("who sees which lessons (one rule with the computer's list)", () => {
  it("admin and viewer see everything; a viewer cannot toggle", () => {
    expect(ids(myScope(lessons, { id: "ad", role: "admin" }))).toEqual([1, 2, 3, 4]);
    expect(ids(scopedLessons(lessons, { id: "v", role: "viewer" }, false))).toEqual([1, 2, 3, 4]);
    expect(canToggleScope({ id: "v", role: "viewer" })).toBe(false);
    expect(canToggleScope({ id: "ad", role: "admin" })).toBe(false);
  });

  it("a referent sees what was distributed to them and their own", () => {
    expect(ids(myScope(lessons, { id: "r1", role: "referent" }))).toEqual([2, 3]);
  });

  it("anyone else sees what they created, and everything with 'all'", () => {
    const pm = { id: "pm", role: "project_manager" };
    expect(ids(scopedLessons(lessons, pm, false))).toEqual([1]);
    expect(ids(scopedLessons(lessons, pm, true))).toEqual([1, 2, 3, 4]);
    expect(canToggleScope(pm)).toBe(true);
  });
});

describe("phone navigation", () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("the wizard has no tab bar; the browsing screens do", () => {
    expect(showsTabBar("/m")).toBe(true);
    expect(showsTabBar("/m/lessons")).toBe(true);
    expect(showsTabBar("/m/lessons/12")).toBe(true);
    expect(showsTabBar("/m/drafts")).toBe(true);
    expect(showsTabBar("/m/new")).toBe(false);
    expect(showsTabBar("/m/lessons/12/edit")).toBe(false);
  });

  it("a phone opens the phone home until its user asks for the computer layout", () => {
    vi.spyOn(window, "matchMedia").mockImplementation((q: string) => ({ matches: q.includes("max-width") } as MediaQueryList));
    expect(opensPhoneHome()).toBe(true);
    setPrefersDesktop(true);
    expect(prefersDesktop()).toBe(true);
    expect(opensPhoneHome()).toBe(false);
    setPrefersDesktop(false);
    expect(opensPhoneHome()).toBe(true);
  });

  it("a computer never does", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(() => ({ matches: false } as MediaQueryList));
    expect(opensPhoneHome()).toBe(false);
  });
});

describe("attachments", () => {
  it("shows pictures as pictures, the rest as a link", () => {
    expect(isImageUrl("https://x/lessons/api/storage/v1/object/public/lesson-files/1_a.jpg")).toBe(true);
    expect(isImageUrl("https://x/a.JPEG?token=1")).toBe(true);
    expect(isImageUrl("https://x/a.pdf")).toBe(false);
    expect(isImageUrl("https://x/jpg-report.docx")).toBe(false);
    expect(isImageUrl(undefined)).toBe(false);
  });
});

describe("dates people read", () => {
  it("are DD/MM/YYYY", async () => {
    const { ilDate } = await import("@/lib/ilDate");
    expect(ilDate("2026-07-16")).toBe("16/07/2026");
    expect(ilDate("2026-07-16T10:00:00Z")).toBe("16/07/2026");
    expect(ilDate("לפני שעה")).toBe("לפני שעה");
    expect(ilDate(undefined)).toBe("");
  });
});
