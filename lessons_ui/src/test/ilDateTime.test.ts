import { describe, expect, it } from "vitest";
import { ilDateTime } from "@/lib/ilDate";

describe("ilDateTime", () => {
  it("renders a UTC timestamp in Israel time as DD/MM/YYYY HH:MM", () => {
    expect(ilDateTime("2026-10-17T10:05:00+00:00")).toBe("17/10/2026 13:05");
    expect(ilDateTime("2026-01-10T22:30:00Z")).toBe("11/01/2026 00:30");
  });
  it("is empty for nothing or garbage", () => {
    expect(ilDateTime(null)).toBe("");
    expect(ilDateTime("not a date")).toBe("");
  });
});
