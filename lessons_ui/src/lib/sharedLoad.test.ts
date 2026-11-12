import { describe, expect, it } from "vitest";
import { sharedLoad } from "./sharedLoad";

describe("sharedLoad", () => {
  it("28 pickers on one screen make one request", async () => {
    let calls = 0;
    const shared = sharedLoad(async () => { calls += 1; return ["אתר א", "אתר ב"]; });
    const results = await Promise.all(Array.from({ length: 28 }, () => shared.load()));
    expect(calls).toBe(1);
    expect(results.every((r) => r === results[0])).toBe(true);
  });

  it("asks again once the result is older than the ttl", async () => {
    let calls = 0;
    let clock = 0;
    const shared = sharedLoad(async () => ++calls, 1000, () => clock);
    expect(await shared.load()).toBe(1);
    clock = 999;
    expect(await shared.load()).toBe(1);
    clock = 1000;
    expect(await shared.load()).toBe(2);
  });

  it("invalidate makes the next caller see fresh data", async () => {
    let calls = 0;
    const shared = sharedLoad(async () => ++calls);
    await shared.load();
    shared.invalidate();
    expect(await shared.load()).toBe(2);
  });

  it("a failed request is not remembered", async () => {
    let calls = 0;
    const shared = sharedLoad(async () => {
      calls += 1;
      if (calls === 1) throw new Error("network");
      return "ok";
    });
    await expect(shared.load()).rejects.toThrow("network");
    expect(await shared.load()).toBe("ok");
  });
});
