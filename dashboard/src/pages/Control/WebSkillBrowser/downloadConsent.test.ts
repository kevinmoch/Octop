import { beforeEach, describe, expect, it } from "vitest";
import { createDownloadConsent } from "../../../../webskill/src/downloadConsent";

describe("Octop download consent", () => {
  beforeEach(() => localStorage.clear());

  it("remembers the download directory action, not a single file", async () => {
    const consent = createDownloadConsent(() => "zh");
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
