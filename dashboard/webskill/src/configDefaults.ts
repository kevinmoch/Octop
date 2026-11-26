import { createLocalStorageRuntimeConfigStore } from "@webskill/chatbot";
import type { RuntimeConfigStore } from "@webskill/chatbot";
import { createConfigDefaultsStore } from "@webskill/chatbot/config";
import type {
  BakedSecretLoader,
  WebSkillConfigDefaults,
} from "@webskill/chatbot/config";

function readJson(key: string): unknown {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? undefined : JSON.parse(raw);
  } catch {
    return undefined;
  }
}

export function createWebSkillRuntimeConfigStore(
  userId: string | number,
  defaults: WebSkillConfigDefaults,
  secrets?: BakedSecretLoader,
): RuntimeConfigStore {
  const runtimeKey = `octop.webskill.runtime.${userId}`;
  const snapshotKey = `octop.webskill.config-defaults.${userId}`;
  return createConfigDefaultsStore(
    createLocalStorageRuntimeConfigStore({ key: runtimeKey }),
    {
      defaults,
      secrets,
      readRaw: () => readJson(runtimeKey),
      snapshot: {
        read: () => readJson(snapshotKey) as WebSkillConfigDefaults | undefined,
        write: (snapshot) => {
          try {
            localStorage.setItem(snapshotKey, JSON.stringify(snapshot));
          } catch {
            // The next load still works; only default-upgrade tracking is lost.
          }
        },
        clear: () => {
          try {
            localStorage.removeItem(snapshotKey);
          } catch {
            // Storage can be unavailable in privacy-restricted contexts.
          }
        },
      },
    },
  );
}
