import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopChromeProvider } from "../hooks/useDesktopChrome";
import type { DesktopChromeStyle } from "../utils/desktopChrome";
import WindowsAppMenu from "./WindowsAppMenu";

const state = vi.hoisted(() => ({
  fullscreen: false,
  linux: false,
  model: vi.fn(async () => ({
    titles: ["File", "Edit", "View", "Help"],
    menus: [
      [
        {
          kind: "command" as const,
          id: "quit",
          label: "Quit",
          accelerator: null,
        },
      ],
      [],
      [],
      [],
    ],
  })),
}));

vi.mock("../hooks/useWindowFullscreen", () => ({
  useWindowFullscreen: () => state.fullscreen,
}));

vi.mock("../utils/desktopBridge", () => ({
  isLinuxDesktopShell: () => state.linux,
  getDesktopBridge: () => ({
    windowsMenu: { model: state.model, invoke: vi.fn() },
  }),
}));

afterEach(() => {
  state.fullscreen = false;
  state.linux = false;
  cleanup();
});

function renderMenu(chrome: DesktopChromeStyle | null) {
  return render(
    <DesktopChromeProvider value={chrome}>
      <WindowsAppMenu />
    </DesktopChromeProvider>,
  );
}

describe("Windows caption row", () => {
  it("keeps the drag strip but hides the menu titles on Windows", () => {
    renderMenu("windows");
    expect(document.querySelector(".octop-win-menu")).toHaveClass(
      "octop-desktop-drag",
    );
    expect(screen.queryAllByRole("menuitem")).toHaveLength(0);
    expect(document.documentElement.dataset.octopCaptionRow).toBe("");
    expect(
      document.documentElement.style.getPropertyValue("--octop-caption-row"),
    ).toBe("32px");
  });

  it("still hides the row on macOS and in the browser", () => {
    const { container, rerender } = renderMenu("mac");
    expect(container.querySelector(".octop-win-menu")).toBeNull();
    rerender(
      <DesktopChromeProvider value={null}>
        <WindowsAppMenu />
      </DesktopChromeProvider>,
    );
    expect(container.querySelector(".octop-win-menu")).toBeNull();
  });

  it("keeps the Linux menu titles", async () => {
    state.linux = true;
    renderMenu("windows");
    expect(
      await screen.findByRole("menuitem", { name: "File" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeInTheDocument();
  });

  it("keeps the empty caption row in fullscreen", () => {
    state.fullscreen = true;
    renderMenu("windows");
    expect(document.querySelector(".octop-win-menu")).not.toBeNull();
    expect(screen.queryAllByRole("menuitem")).toHaveLength(0);
    expect(
      document.documentElement.style.getPropertyValue("--octop-caption-row"),
    ).toBe("32px");
  });
});
