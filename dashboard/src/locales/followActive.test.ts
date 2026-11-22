import { describe, expect, it } from "vitest";
import en from "./en.json";
import zh from "./zh.json";

describe("remoteBrowser.followActive locale entries", () => {
  it("are top-level remoteBrowser keys, not nested under ai", () => {
    expect(zh.remoteBrowser.followActive).toBe("跟随当前页");
    expect(en.remoteBrowser.followActive).toBe("Follow current page");
    expect(zh.webskill.downloadFailureTitle).toContain("下载失败");
    expect(en.webskill.downloadFailureTitle).toContain("Download failed");
  });
});
