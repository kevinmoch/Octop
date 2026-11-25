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
});
