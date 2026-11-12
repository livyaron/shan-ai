import { describe, expect, it } from "vitest";
import { fetchBootstrap, readBootstrap, STARTUP_TABLES } from "./startupData";

const reply = (status: number, body?: unknown) =>
  (async () => ({ status, ok: status >= 200 && status < 300, json: async () => body })) as unknown as typeof fetch;

describe("startup data", () => {
  it("one request brings /me and every table", async () => {
    const tables = Object.fromEntries(STARTUP_TABLES.map((t) => [t, [{ id: 1 }]]));
    const boot = await fetchBootstrap("/lessons/api", reply(200, { me: { profile: { id: "u1" }, groups: [] }, tables, errors: {} }));
    expect(boot.unauthorized).toBe(false);
    expect(boot.me?.profile.id).toBe("u1");
    expect(Object.keys(boot.tables).sort()).toEqual([...STARTUP_TABLES].sort());
  });

  it("a table the server could not read is simply missing, so the app asks for it directly", () => {
    const boot = readBootstrap({ me: { profile: { id: "u1" } }, tables: { lessons: [], projects: "oops" }, errors: { feedback: "status 500" } });
    expect("lessons" in boot.tables).toBe(true);
    expect("projects" in boot.tables).toBe(false);
    expect("feedback" in boot.tables).toBe(false);
  });

  it("an unknown table name is ignored", () => {
    expect(Object.keys(readBootstrap({ tables: { users: [{ password: "x" }] } }).tables)).toEqual([]);
  });

  it("no session means the login page, not a fallback", async () => {
    expect((await fetchBootstrap("/lessons/api", reply(401))).unauthorized).toBe(true);
  });

  it("an older server or a network error falls back to the table-by-table path", async () => {
    expect(await fetchBootstrap("/lessons/api", reply(404))).toEqual({ unauthorized: false, me: null, tables: {} });
    const failing = (async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    expect(await fetchBootstrap("/lessons/api", failing)).toEqual({ unauthorized: false, me: null, tables: {} });
  });
});
