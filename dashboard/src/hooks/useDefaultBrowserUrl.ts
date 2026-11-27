import { useEffect, useState } from "react";
import { octopSettingsApi } from "../api/modules/settings";

/** Used when config.json has no usable `desktop_browser_url`. */
export const FALLBACK_DESKTOP_BROWSER_URL = "https://jijian.huawei.com/";

let cachedUrl: string | null = null;
let inflight: Promise<string> | null = null;

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export function applyDesktopBrowserFetchResult(
  result: { ok: true; defaultUrl: unknown } | { ok: false },
): {
  cache: string | null;
  value: string;
} {
  if (!result.ok) {
    return { cache: null, value: FALLBACK_DESKTOP_BROWSER_URL };
  }
  const raw =
    typeof result.defaultUrl === "string" ? result.defaultUrl.trim() : "";
  if (!raw || !isHttpUrl(raw)) {
    return { cache: null, value: FALLBACK_DESKTOP_BROWSER_URL };
  }
  return { cache: raw, value: raw };
}

async function fetchDefaultBrowserUrl(): Promise<string> {
  if (cachedUrl) return cachedUrl;
  if (!inflight) {
    inflight = octopSettingsApi
      .desktopBrowser()
      .then((settings) => {
        const next = applyDesktopBrowserFetchResult({
          ok: true,
          defaultUrl: settings.default_url,
        });
        cachedUrl = next.cache;
        return next.value;
      })
      .catch(() => {
        const next = applyDesktopBrowserFetchResult({ ok: false });
        cachedUrl = next.cache;
        return next.value;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/**
 * Start page for local browser tabs, from config.json `desktop_browser_url`
 * (via `GET /api/settings/desktop-browser`).
 *
 * Returns null until the first response so the caller can hold off creating a
 * tab - otherwise the first tab would always open the hard-coded fallback.
 */
export function useDefaultBrowserUrl(): string | null {
  const [url, setUrl] = useState<string | null>(cachedUrl);

  useEffect(() => {
    void fetchDefaultBrowserUrl().then(setUrl);
  }, []);

  return url;
}
