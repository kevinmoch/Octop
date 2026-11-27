import { useCallback, useEffect, useState } from "react";
import {
  getDesktopBridge,
  type DesktopBrowserMode,
} from "../utils/desktopBridge";

export interface UseDesktopBrowserModeResult {
  /** Browser the desktop page should render. Non-desktop always reports remote. */
  mode: DesktopBrowserMode;
  /** False until the shell's persisted preference has been read. */
  ready: boolean;
  /** Persist the preference; the shell may override it via env (see doc §7.3). */
  setMode(mode: DesktopBrowserMode): Promise<void>;
}

/**
 * Resolves whether Control → Browser shows local tabs or the streaming viewer.
 *
 * The preference lives in the desktop shell's client settings, so an emergency
 * rollback is one env var (`OCTOP_DESKTOP_BROWSER_MODE=remote`) away even when
 * the Dashboard cannot be changed.
 */
export function useDesktopBrowserMode(): UseDesktopBrowserModeResult {
  const bridge = getDesktopBridge();
  const [mode, setModeState] = useState<DesktopBrowserMode>(
    bridge ? "local" : "remote",
  );
  const [ready, setReady] = useState(!bridge);

  useEffect(() => {
    if (!bridge) return;
    let cancelled = false;
    void bridge.clientSettings
      .get()
      .then((settings) => {
        if (cancelled) return;
        setModeState(settings.browserMode ?? "local");
        setReady(true);
      })
      .catch(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [bridge]);

  const setMode = useCallback(
    async (next: DesktopBrowserMode) => {
      // Switch the page immediately; persistence is best effort (an old shell
      // may not know this key yet).
      setModeState(next);
      try {
        await bridge?.clientSettings.set({ browserMode: next });
      } catch {
        // Keep the in-session switch even when persistence fails.
      }
    },
    [bridge],
  );

  return { mode, ready, setMode };
}
