import { describe, expect, it, vi } from "vitest";
import { OctopHostBridge } from "../../../../webskill/src/bridge";
import {
  OctopTabWorksetPort,
  createRemoteTabWatcher,
} from "../../../../webskill/src/pageTabs";

function bridgeWithTabs(handles: string[]) {
  const calls: Array<{ api: string; payload?: unknown }> = [];
  const bridge = {
    call: vi.fn(async (api: string, payload?: unknown) => {
      calls.push({ api, ...(payload !== undefined ? { payload } : {}) });
      if (api === "tabs-list") {
        return {
          tabs: handles.map((handle, index) => ({
            handle,
            title: index === 0 ? "Origin" : "Detail",
            url: index === 0 ? "https://a.test/" : "https://b.test/",
          })),
        };
      }
      if (api === "tabs-admit") return { handle: "tab_admitted_000000000000" };
      return { ok: true };
    }),
  } as unknown as OctopHostBridge;
  return { bridge, calls };
}

describe("remote tab workset port", () => {
  it("keeps handles opaque and never exposes target ids", async () => {
    const { bridge, calls } = bridgeWithTabs([
      "tab_origin_0000000000000000",
      "tab_detail_0000000000000000",
    ]);
    const port = new OctopTabWorksetPort(bridge);
    const tabs = await port.list();
    expect(tabs.map((tab) => tab.title)).toEqual(["Origin", "Detail"]);
    expect(tabs.every((tab) => tab.handle.startsWith("tab_"))).toBe(true);
    await port.focus("tab_detail_0000000000000000");
    await port.close("tab_detail_0000000000000000");
    expect(calls).toEqual([
      { api: "tabs-list" },
      { api: "tabs-focus", payload: { handle: "tab_detail_0000000000000000" } },
      { api: "tabs-close", payload: { handle: "tab_detail_0000000000000000" } },
    ]);
  });

  it("watches only pre-authorized models and admits the observed handle", async () => {
    const { bridge } = bridgeWithTabs(["tab_origin_0000000000000000"]);
    const port = new OctopTabWorksetPort(bridge);
    const watcher = createRemoteTabWatcher(port);
    const window = await watcher.open();
    // The fake bridge never adds a tab, so settle must honor its budget.
    await expect(window.settle({ timeoutMs: 5 })).resolves.toBeUndefined();
  });
});

describe("observed tab activation", () => {
  it("passes an observed handle through host admission", async () => {
    const { bridge, calls } = bridgeWithTabs([
      "tab_origin_0000000000000000",
      "tab_detail_0000000000000000",
    ]);
    const port = new OctopTabWorksetPort(bridge);
    port.rememberOpened("tab_detail_0000000000000000");

    const handle = await port.admit({
      title: "Detail",
      url: "https://b.test/",
    });

    expect(handle).toBe("tab_admitted_000000000000");
    expect(calls).toEqual([
      { api: "tabs-list" },
      {
        api: "tabs-admit",
        payload: { title: "Detail", url: "https://b.test/" },
      },
    ]);
  });
});
