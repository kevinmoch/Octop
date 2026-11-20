import { beforeEach, describe, expect, it } from "vitest";
import { createPageActionConsent } from "../../../../webskill/src/pageActionConsent";

describe("Octop page-action consent", () => {
  beforeEach(() => localStorage.clear());

  it("recalls a remembered origin+action and describes the real scope", async () => {
    const consent = createPageActionConsent({
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

  it("does not remember without a known origin", async () => {
    const consent = createPageActionConsent({
      origin: () => undefined,
      locale: () => "en",
    });
    await consent.remember({ role: "link" }, "click");
    expect(await consent.recall({ role: "link" }, "click")).toBe(false);
  });
});
