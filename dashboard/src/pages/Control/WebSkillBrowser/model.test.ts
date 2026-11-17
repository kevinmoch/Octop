// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
type ConfigStore = Parameters<typeof createHostLlmClient>[0];
type Config = Awaited<ReturnType<ConfigStore["load"]>>;
import {
  createHostLlmClient,
  isLoopbackNoAuthEndpoint,
  LocalNoAuthRegistry,
  type RuntimeLlmEntry,
} from "../../../../webskill/src/model";

function model(baseUrl: string, apiKey = ""): RuntimeLlmEntry {
  return {
    id: "local-model",
    label: "Local model",
    provider: "openai-compatible",
    baseUrl,
    model: "test-model",
    apiKey,
  };
}

describe("WebSkill local no-auth host boundary", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("requires an explicit marker on a loopback OpenAI-compatible endpoint", () => {
    expect(isLoopbackNoAuthEndpoint(model("http://127.0.0.1:1234/v1"))).toBe(
      true,
    );
    expect(isLoopbackNoAuthEndpoint(model("http://localhost:1234/v1"))).toBe(
      true,
    );
    expect(
      isLoopbackNoAuthEndpoint(model("https://models.example.com/v1")),
    ).toBe(false);
    expect(isLoopbackNoAuthEndpoint(model("http://192.168.1.20:1234/v1"))).toBe(
      false,
    );
    expect(
      isLoopbackNoAuthEndpoint(model("http://localhost:1234/v1", "real-key")),
    ).toBe(false);
  });

  it("persists only the opt-in marker, not a credential", () => {
    const registry = new LocalNoAuthRegistry(7);
    registry.set("local-model", true);
    expect(new LocalNoAuthRegistry(7).has("local-model")).toBe(true);
    expect(localStorage.getItem("octop.webskill.local-no-auth.7")).toBe(
      '["local-model"]',
    );
    registry.set("local-model", false);
    expect(registry.has("local-model")).toBe(false);
  });

  it("applies the compatibility value only to the opted-in real request", async () => {
    const config = {
      llm: {
        entries: [model("http://127.0.0.1:1234/v1")],
        defaultId: "local-model",
      },
    } as Config;
    const store: ConfigStore = {
      load: async () => config,
      save: async () => {},
      reset: async () => {},
      subscribe: () => () => {},
    };
    const registry = new LocalNoAuthRegistry(1);
    registry.set("local-model", true);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          choices: [{ message: { role: "assistant", content: "ok" } }],
          usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await createHostLlmClient(store, registry).complete({
      messages: [{ role: "user", content: [{ type: "text", text: "test" }] }],
    });
    expect(result.content?.[0]).toEqual({ type: "text", text: "ok" });
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(request.headers).get("authorization")).toBe(
      "Bearer octop-local-no-auth",
    );
    expect(JSON.stringify(await store.load())).not.toContain(
      "octop-local-no-auth",
    );
  });
});
