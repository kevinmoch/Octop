// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { createWebSkillRuntimeConfigStore } from "../../../../webskill/src/configDefaults";

type ConfigDefaults = Parameters<typeof createWebSkillRuntimeConfigStore>[1];

function defaults(limit: number, includeSkills: boolean): ConfigDefaults {
  const items = [
    {
      id: "qp-summarize",
      text: { zh: "总结当前页面", en: "Summarize this page" },
      icon: "page" as const,
    },
  ];
  if (includeSkills) {
    items.push({
      id: "qp-skills",
      text: { zh: "有哪些可用技能？", en: "What skills are available?" },
      icon: "list" as const,
    });
  }
  return { quickPrompts: items, quickPromptLimit: limit };
}

describe("WebSkill config defaults", () => {
  afterEach(() => localStorage.clear());

  it("keeps user edits while upgrading untouched defaults", async () => {
    const initialStore = createWebSkillRuntimeConfigStore(
      7,
      defaults(8, false),
    );
    const initial = await initialStore.load();
    expect(initial.quickPromptLimit).toBe(8);
    expect(initial.quickPrompts.map((item) => item.id)).toEqual([
      "qp-summarize",
    ]);

    await initialStore.save({ ...initial, quickPromptLimit: 3 });
    const upgraded = await createWebSkillRuntimeConfigStore(
      7,
      defaults(9, true),
    ).load();

    expect(upgraded.quickPromptLimit).toBe(3);
    expect(upgraded.quickPrompts.map((item) => item.id)).toEqual([
      "qp-summarize",
      "qp-skills",
    ]);
  });

  it("isolates runtime configuration and default snapshots by user", async () => {
    const first = createWebSkillRuntimeConfigStore(1, defaults(8, false));
    const firstConfig = await first.load();
    await first.save({ ...firstConfig, quickPromptLimit: 2 });

    const second = await createWebSkillRuntimeConfigStore(
      2,
      defaults(8, true),
    ).load();
    expect(second.quickPromptLimit).toBe(8);
    expect(second.quickPrompts.map((item) => item.id)).toEqual([
      "qp-summarize",
      "qp-skills",
    ]);
  });

  it("restores a baked model key without persisting it", async () => {
    const modelDefaults: ConfigDefaults = {
      ...defaults(8, false),
      llm: {
        entries: [
          {
            id: "deepseek-1",
            label: "DeepSeek",
            provider: "openai-compatible",
            baseUrl: "https://api.deepseek.com",
            model: "deepseek-flash",
          },
        ],
        defaultId: "deepseek-1",
      },
    };
    const store = createWebSkillRuntimeConfigStore(
      7,
      modelDefaults,
      async () => ({ "deepseek-1": "sk-test" }),
    );

    const loaded = await store.load();
    expect(loaded.llm.defaultId).toBe("deepseek-1");
    expect(loaded.llm.entries[0]).toMatchObject({
      model: "deepseek-flash",
      apiKey: "sk-test",
    });

    await store.save(loaded);
    const raw = localStorage.getItem("octop.webskill.runtime.7") ?? "";
    expect(raw).not.toContain("sk-test");
    expect(raw).toContain("webskill:baked-api-key");
    expect((await store.load()).llm.entries[0].apiKey).toBe("sk-test");
  });
});
