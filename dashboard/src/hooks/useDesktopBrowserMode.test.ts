import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useDesktopBrowserMode } from "./useDesktopBrowserMode";
import type {
  DesktopClientSettings,
  OctopDesktopBridge,
} from "../utils/desktopBridge";

function installShell(settings: Partial<DesktopClientSettings>) {
  const get = vi.fn(async () => ({
    launchAtLogin: false,
    closeBehavior: "tray",
    themeBackground: "system",
    stableOnly: true,
    ...settings,
  }));
  const set = vi.fn(async (patch: Partial<DesktopClientSettings>) => ({
    launchAtLogin: false,
    closeBehavior: "tray",
    themeBackground: "system",
    stableOnly: true,
    ...settings,
    ...patch,
  }));
  window.octopDesktop = {
    clientSettings: { get, set },
  } as unknown as OctopDesktopBridge;
  return { get, set };
}

describe("useDesktopBrowserMode", () => {
  it("is remote and ready outside the desktop shell", () => {
    delete (window as unknown as { octopDesktop?: unknown }).octopDesktop;
    const { result } = renderHook(() => useDesktopBrowserMode());
    expect(result.current.mode).toBe("remote");
    expect(result.current.ready).toBe(true);
  });

  it("reads the persisted local default from the shell", async () => {
    const { get } = installShell({ browserMode: "local" });
    const { result } = renderHook(() => useDesktopBrowserMode());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.mode).toBe("local");
    expect(get).toHaveBeenCalled();
  });

  it("honours a persisted remote preference", async () => {
    installShell({ browserMode: "remote" });
    const { result } = renderHook(() => useDesktopBrowserMode());
    await waitFor(() => expect(result.current.mode).toBe("remote"));
  });

  it("falls back to local for a shell without the setting", async () => {
    installShell({});
    const { result } = renderHook(() => useDesktopBrowserMode());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.mode).toBe("local");
  });

  it("switches immediately and persists the preference", async () => {
    const { set } = installShell({ browserMode: "local" });
    const { result } = renderHook(() => useDesktopBrowserMode());
    await waitFor(() => expect(result.current.ready).toBe(true));

    await act(async () => {
      await result.current.setMode("remote");
    });
    expect(result.current.mode).toBe("remote");
    expect(set).toHaveBeenCalledWith({ browserMode: "remote" });
  });
});
