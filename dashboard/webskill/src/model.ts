import { WebSkillError } from "@webskill/sdk";
import type {
  LlmClient,
  LlmCompleteInput,
  LlmStreamEvent,
} from "@webskill/sdk";
import { createLlmClient } from "@webskill/sdk/browser";
import type { RuntimeConfig, RuntimeConfigStore } from "@webskill/chatbot";
export type RuntimeLlmEntry = RuntimeConfig["llm"]["entries"][number];

export function isLoopbackNoAuthEndpoint(entry: RuntimeLlmEntry): boolean {
  if (
    entry.provider !== "openai-compatible" ||
    (entry.apiKey ?? "").trim() !== ""
  )
    return false;
  try {
    const url = new URL(entry.baseUrl ?? "");
    const hostname = url.hostname.toLowerCase();
    return (
      url.protocol === "http:" &&
      (hostname === "localhost" ||
        hostname.endsWith(".localhost") ||
        hostname === "[::1]" ||
        /^127(?:\.\d{1,3}){3}$/.test(hostname))
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
  const client = async () =>
    createLlmClient(resolveEntry(await store.load(), noAuth));
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
