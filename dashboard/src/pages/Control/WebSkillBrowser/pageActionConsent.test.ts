import { beforeEach, describe, expect, it } from "vitest";
import { createPageActionConsent } from "../../../../webskill/src/pageActionConsent";

describe("CCS Buddy page-action consent", () => {
  beforeEach(() => localStorage.clear());

  it("recalls a remembered origin+action and describes the real scope", async () => {
    const consent = createPageActionConsent({
      userId: 1,
      origin: () => "https://ccs.huaweicloud.com",
      locale: () => "zh",
    });
    expect(await consent.recall({ role: "link" }, "click")).toBe(false);
    await consent.remember({ role: "link" }, "click");
    expect(await consent.recall({ role: "link" }, "click")).toBe(true);
    expect(consent.describeScope?.({ role: "link" }, "click")).toContain(
      "https://ccs.huaweicloud.com",
    );
  });

  it("isolates remembered actions by CCS Buddy user id", async () => {
    const a = createPageActionConsent({
      userId: 1,
      origin: () => "https://ccs.huaweicloud.com",
      locale: () => "en",
    });
    const b = createPageActionConsent({
      userId: 2,
      origin: () => "https://ccs.huaweicloud.com",
      locale: () => "en",
    });
    await a.remember({ role: "link" }, "click");
    expect(await a.recall({ role: "link" }, "click")).toBe(true);
    expect(await b.recall({ role: "link" }, "click")).toBe(false);
    expect(await b.list()).toEqual([]);
    await b.remember({ role: "link" }, "click");
    await a.forget("1|https://ccs.huaweicloud.com|click");
    expect(await a.recall({ role: "link" }, "click")).toBe(false);
    expect(await b.recall({ role: "link" }, "click")).toBe(true);
  });

  it("does not remember without a known origin", async () => {
    const consent = createPageActionConsent({
      userId: 1,
      origin: () => undefined,
      locale: () => "en",
    });
    await consent.remember({ role: "link" }, "click");
    expect(await consent.recall({ role: "link" }, "click")).toBe(false);
  });
});
