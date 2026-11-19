import { WebSkillError } from "@webskill/sdk";
import type {
  LlmClient,
  LlmCompleteInput,
  LlmStreamEvent,
} from "@webskill/sdk";
import { createLlmClient } from "@webskill/sdk/browser";
import type { RuntimeConfig, RuntimeConfigStore } from "@webskill/chatbot";
export type RuntimeLlmEntry = RuntimeConfig["llm"]["entries"][number];

type AddressSpace = "public" | "local" | "loopback";
type LnaPermission = "local-network" | "loopback-network";

function ipv4Space(values: number[]): AddressSpace {
  if (values.length !== 4 || values.some((part) => part > 255)) return "public";
  if (values[0] === 127) return "loopback";
  if (
    values[0] === 0 ||
    values[0] === 10 ||
    (values[0] === 100 && values[1] >= 64 && values[1] <= 127) ||
    (values[0] === 172 && values[1] >= 16 && values[1] <= 31) ||
    (values[0] === 192 && values[1] === 168) ||
    (values[0] === 169 && values[1] === 254)
  )
    return "local";
  return "public";
}

function ipv6Words(host: string): number[] | undefined {
  const halves = host.split("::");
  if (halves.length > 2) return;
  const left = halves[0] ? halves[0].split(":") : [];
  const right = halves[1] ? halves[1].split(":") : [];
  if (
    [...left, ...right].some((part) => !/^[\da-f]{1,4}$/.test(part)) ||
    (halves.length === 1 && left.length !== 8) ||
    left.length + right.length >= 8
  )
    return;
  const words = [
    ...left,
    ...(halves.length === 2
      ? Array.from({ length: 8 - left.length - right.length }, () => "0")
      : []),
    ...right,
  ];
  return words.map((part) => Number.parseInt(part, 16));
}

function addressSpace(hostname: string): AddressSpace {
  const host = hostname
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
  if (host === "localhost" || host.endsWith(".localhost")) return "loopback";
  const octets = host.split(".");
  if (octets.length === 4 && octets.every((part) => /^\d+$/.test(part)))
    return ipv4Space(octets.map(Number));
  const words = ipv6Words(host);
  if (words) {
    if (words.slice(0, 7).every((word) => word === 0) && words[7] === 1)
      return "loopback";
    if (words.slice(0, 5).every((word) => word === 0) && words[5] === 0xffff)
      return ipv4Space([
        words[6] >> 8,
        words[6] & 255,
        words[7] >> 8,
        words[7] & 255,
      ]);
    if (
      words.every((word) => word === 0) ||
      (words[0] & 0xfe00) === 0xfc00 ||
      (words[0] & 0xffc0) === 0xfe80
    )
      return "local";
  }
  if (host.endsWith(".local")) return "local";
  return "public";
}

function requiredLnaPermission(
  entry: RuntimeLlmEntry,
  source: URL,
): LnaPermission | null {
  if (entry.provider === "chrome-builtin") return null;
  try {
    const target = addressSpace(new URL(entry.baseUrl ?? "").hostname);
    const origin = addressSpace(source.hostname);
    if (target === "loopback" && origin !== "loopback")
      return "loopback-network";
    if (target === "local" && origin === "public") return "local-network";
  } catch {
    return null;
  }
  return null;
}

async function assertLnaAllowed(entry: RuntimeLlmEntry): Promise<void> {
  const permission = requiredLnaPermission(
    entry,
    new URL(window.location.href),
  );
  if (!permission || !navigator.permissions?.query) return;
  let state: PermissionState;
  try {
    const status = await navigator.permissions.query({
      name: permission as PermissionName,
    });
    state = status.state;
  } catch {
    return;
  }
  if (state === "denied") {
    throw new WebSkillError(
      "LLM_REQUEST_FAILED",
      `Browser ${permission} permission is denied. Allow local network access in the site settings before using this model.`,
    );
  }
}

export function isLoopbackNoAuthEndpoint(entry: RuntimeLlmEntry): boolean {
  if (
    entry.provider !== "openai-compatible" ||
    (entry.apiKey ?? "").trim() !== ""
  )
    return false;
  try {
    const url = new URL(entry.baseUrl ?? "");
    return (
      url.protocol === "http:" && addressSpace(url.hostname) === "loopback"
    );
  } catch {
    return false;
  }
}

export class LocalNoAuthRegistry {
  constructor(private readonly userId: number) {}

  private get key(): string {
    return `octop.webskill.local-no-auth.${this.userId}`;
  }

  has(id: string): boolean {
    try {
      const stored: unknown = JSON.parse(
        localStorage.getItem(this.key) ?? "[]",
      );
      return Array.isArray(stored) && stored.includes(id);
    } catch {
      return false;
    }
  }

  set(id: string, enabled: boolean): void {
    const current: unknown = JSON.parse(localStorage.getItem(this.key) ?? "[]");
    const ids = new Set(
      Array.isArray(current)
        ? current.filter((id): id is string => typeof id === "string")
        : [],
    );
    if (enabled) ids.add(id);
    else ids.delete(id);
    localStorage.setItem(this.key, JSON.stringify([...ids]));
  }
}

function resolveEntry(
  config: Awaited<ReturnType<RuntimeConfigStore["load"]>>,
  noAuth: LocalNoAuthRegistry,
): RuntimeLlmEntry {
  const active =
    config.llm.entries.find((entry) => entry.id === config.llm.defaultId) ??
    config.llm.entries[0];
  if (!active)
    throw new WebSkillError(
      "LLM_UNAVAILABLE",
      "Configure a WebSkill model in the Console. Octop models are not used as a fallback.",
    );
  if (active.provider !== "chrome-builtin" && !(active.baseUrl ?? "").trim()) {
    throw new WebSkillError(
      "LLM_UNAVAILABLE",
      "The selected WebSkill model has no endpoint. Configure it in the Console.",
    );
  }
  if (isLoopbackNoAuthEndpoint(active) && noAuth.has(active.id)) {
    // Compatibility only for an explicitly opted-in local endpoint. Never persist or display this value.
    return { ...active, apiKey: "octop-local-no-auth" };
  }
  return active;
}

/** Resolve on each request so Console model changes take effect without leaking a compatibility key into its editor. */
export function createHostLlmClient(
  store: RuntimeConfigStore,
  noAuth: LocalNoAuthRegistry,
): LlmClient {
  const client = async () => {
    const entry = resolveEntry(await store.load(), noAuth);
    await assertLnaAllowed(entry);
    return createLlmClient(entry);
  };
  return {
    complete: async (input: LlmCompleteInput) =>
      (await client()).complete(input),
    stream: async function* (
      input: LlmCompleteInput,
    ): AsyncIterable<LlmStreamEvent> {
      const resolved = await client();
      if (!resolved.stream) {
        const response = await resolved.complete(input);
        for (const part of response.content ?? []) {
          if (part.type === "text")
            yield { type: "text-delta", delta: part.text };
        }
        yield {
          type: "done",
          ...(response.usage !== undefined ? { usage: response.usage } : {}),
        };
        return;
      }
      yield* resolved.stream(input);
    },
  };
}
