import { describe, expect, it } from "vitest";
import {
  FALLBACK_DESKTOP_BROWSER_URL,
  applyDesktopBrowserFetchResult,
} from "./useDefaultBrowserUrl";

describe("applyDesktopBrowserFetchResult", () => {
  it("accepts an http(s) start page", () => {
    expect(
      applyDesktopBrowserFetchResult({
        ok: true,
        defaultUrl: " https://intranet.example/start ",
      }),
    ).toEqual({
      cache: "https://intranet.example/start",
      value: "https://intranet.example/start",
    });
  });

  it("falls back for a missing or non-http value", () => {
    for (const defaultUrl of [
      undefined,
      "",
      "javascript:alert(1)",
      "not a url",
    ]) {
      expect(applyDesktopBrowserFetchResult({ ok: true, defaultUrl })).toEqual({
        cache: null,
        value: FALLBACK_DESKTOP_BROWSER_URL,
      });
    }
  });

  it("does not cache a failed fetch", () => {
    expect(applyDesktopBrowserFetchResult({ ok: false })).toEqual({
      cache: null,
      value: FALLBACK_DESKTOP_BROWSER_URL,
    });
  });
});
