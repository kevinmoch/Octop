import type {
  ActionTabWatcher,
  ActionTabWindow,
  OpenedPageInfo,
  TabWorksetPort,
  WorksetTab,
} from "@webskill/sdk/agent";
import { WebSkillError } from "@webskill/sdk";
import type { OctopHostBridge } from "./bridge";
import type { LocalBrowserHost } from "./localHost";

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function asTab(value: unknown): WorksetTab | undefined {
  const record = asRecord(value);
  if (!record || typeof record.handle !== "string" || !record.handle) return;
  return {
    handle: record.handle,
    ...(typeof record.title === "string" ? { title: record.title } : {}),
    ...(typeof record.url === "string" ? { url: record.url } : {}),
  };
}

/**
 * Remote TabWorksetPort. Handles are opaque and issued by the CCS Buddy host; this
 * module never sees a CDP target id. focus() intentionally does not call any
 * browser API — the host moves only its logical modelTarget.
 */
export class OctopTabWorksetPort implements TabWorksetPort {
  private pending?: string;

  constructor(private readonly bridge: OctopHostBridge) {}

  async list(): Promise<readonly WorksetTab[]> {
    const result = asRecord(await this.bridge.call("tabs-list"));
    if (!result || !Array.isArray(result.tabs)) {
      throw new WebSkillError(
        "TOOL_EXECUTION_FAILED",
        "The host tab list is invalid",
      );
    }
    const tabs = result.tabs.map(asTab);
    if (tabs.some((tab) => tab === undefined)) {
      throw new WebSkillError(
        "TOOL_EXECUTION_FAILED",
        "The host tab list is invalid",
      );
    }
    return tabs as WorksetTab[];
  }

  async focus(handle: string): Promise<void> {
    await this.bridge.call("tabs-focus", { handle });
  }

  async close(handle: string): Promise<void> {
    await this.bridge.call("tabs-close", { handle });
    if (this.pending === handle) this.pending = undefined;
  }

  async admit(opened: OpenedPageInfo): Promise<string> {
    if (this.pending !== undefined) {
      const tab = (await this.list()).find(
        (item) => item.handle === this.pending,
      );
      if (tab !== undefined) {
        this.pending = undefined;
        const result = asRecord(
          await this.bridge.call("tabs-admit", {
            ...(tab.title !== undefined ? { title: tab.title } : {}),
            ...(tab.url !== undefined ? { url: tab.url } : {}),
          }),
        );
        if (!result || typeof result.handle !== "string" || !result.handle) {
          throw new WebSkillError(
            "TOOL_EXECUTION_FAILED",
            "The host did not admit the page",
          );
        }
        return result.handle;
      }
      this.pending = undefined;
    }
    const result = asRecord(
      await this.bridge.call("tabs-admit", {
        ...(opened.title !== undefined ? { title: opened.title } : {}),
        ...(opened.url !== undefined ? { url: opened.url } : {}),
      }),
    );
    if (!result || typeof result.handle !== "string" || !result.handle) {
      throw new WebSkillError(
        "TOOL_EXECUTION_FAILED",
        "The host did not admit the page",
      );
    }
    return result.handle;
  }

  async reset(): Promise<void> {
    this.pending = undefined;
    await this.bridge.call("tabs-reset");
  }

  rememberOpened(handle: string): void {
    this.pending = handle;
  }
}

/**
 * Watches only the already-authorized model workset. A page the user opens
 * manually is never observed here, and a new page still has to pass
 * TabWorksetPolicy.admit before the model can focus it.
 */
export function createRemoteTabWatcher(
  port: OctopTabWorksetPort,
): ActionTabWatcher {
  return {
    async open(): Promise<ActionTabWindow> {
      const before = new Set((await port.list()).map((tab) => tab.handle));
      let settled = false;
      return {
        settle: async ({ timeoutMs }) => {
          if (settled) return;
          settled = true;
          const deadline = Date.now() + Math.max(0, timeoutMs);
          while (true) {
            const fresh = (await port.list()).find(
              (tab) => !before.has(tab.handle),
            );
            if (fresh !== undefined) {
              port.rememberOpened(fresh.handle);
              return {
                ...(fresh.title !== undefined ? { title: fresh.title } : {}),
                ...(fresh.url !== undefined ? { url: fresh.url } : {}),
              };
            }
            if (Date.now() >= deadline) return;
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
        },
      };
    },
  };
}

/**
 * TabWorksetPort backed by the Electron shell's local browser (design doc
 * §4.5.2). `focus` moves only the agent's logical target; the tab the user sees
 * does not change.
 */
export class OctopLocalTabWorksetPort implements TabWorksetPort {
  private pending?: string;
  private focused?: string;

  constructor(private readonly host: LocalBrowserHost) {}

  async list(): Promise<readonly WorksetTab[]> {
    const tabs = await this.host.tabs.list();
    if (!Array.isArray(tabs)) {
      throw new WebSkillError(
        "TOOL_EXECUTION_FAILED",
        "The host tab list is invalid",
      );
    }
    return tabs
      .filter(
        (tab) =>
          typeof tab?.id === "string" &&
          tab.id.length > 0 &&
          tab.id.length <= 80,
      )
      .map((tab) => ({
        handle: tab.id,
        ...(typeof tab.title === "string" ? { title: tab.title } : {}),
        ...(typeof tab.url === "string" ? { url: tab.url } : {}),
      }));
  }

  async focus(handle: string): Promise<void> {
    this.focused = handle;
  }

  async close(handle: string): Promise<void> {
    await this.host.tabs.close(handle);
    if (this.pending === handle) this.pending = undefined;
    if (this.focused === handle) this.focused = undefined;
  }

  async admit(opened: OpenedPageInfo): Promise<string> {
    // A tab opened by the page itself (`window.open`, `target=_blank`) already
    // exists: the main process created it, so reuse it instead of opening twice.
    if (this.pending !== undefined) {
      const existing = (await this.list()).find(
        (tab) => tab.handle === this.pending,
      );
      this.pending = undefined;
      if (existing !== undefined) return existing.handle;
    }
    if (opened.url === undefined || opened.url.length === 0) {
      throw new WebSkillError(
        "TOOL_EXECUTION_FAILED",
        "The opened page has no URL",
      );
    }
    return this.host.tabs.create({ url: opened.url, active: false });
  }

  async reset(): Promise<void> {
    this.pending = undefined;
    this.focused = undefined;
  }

  rememberOpened(handle: string): void {
    this.pending = handle;
  }

  /** Agent focus, not the user's visible tab. */
  focusedHandle(): string | undefined {
    return this.focused;
  }
}

export function createLocalTabWatcher(
  port: OctopLocalTabWorksetPort,
): ActionTabWatcher {
  return {
    async open(): Promise<ActionTabWindow> {
      const before = new Set((await port.list()).map((tab) => tab.handle));
      let settled = false;
      return {
        settle: async ({ timeoutMs }) => {
          if (settled) return;
          settled = true;
          const deadline = Date.now() + Math.max(0, timeoutMs);
          while (true) {
            const fresh = (await port.list()).find(
              (tab) => !before.has(tab.handle),
            );
            if (fresh !== undefined) {
              port.rememberOpened(fresh.handle);
              return {
                ...(fresh.title !== undefined ? { title: fresh.title } : {}),
                ...(fresh.url !== undefined ? { url: fresh.url } : {}),
              };
            }
            if (Date.now() >= deadline) return;
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
        },
      };
    },
  };
}
