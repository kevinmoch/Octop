import { beforeEach, describe, expect, it } from "vitest";
import { createDownloadConsent } from "../../../../webskill/src/downloadConsent";

describe("Octop download consent", () => {
  beforeEach(() => localStorage.clear());

  it("isolates remembered download actions by Octop user id", async () => {
    const a = createDownloadConsent({ userId: 1, locale: () => "en" });
    const b = createDownloadConsent({ userId: 2, locale: () => "en" });
    await a.remember("read");
    expect(await a.recall("read")).toBe(true);
    expect(await b.recall("read")).toBe(false);
    expect(await b.list()).toEqual([]);
    await b.remember("read");
    await a.forgetAll();
    expect(await a.recall("read")).toBe(false);
    expect(await b.recall("read")).toBe(true);
  });

  it("remembers the download directory action, not a single file", async () => {
    const consent = createDownloadConsent({ userId: 1, locale: () => "zh" });
    expect(await consent.recall("read")).toBe(false);
    await consent.remember("read", {
      id: "one-file",
      name: "a.txt",
      contentType: "text/plain",
      size: 1,
      at: "2026-01-01T00:00:00Z",
    });
    expect(await consent.recall("read")).toBe(true);
    expect(consent.describeScope?.("read")).toContain("下载");
  });
});
