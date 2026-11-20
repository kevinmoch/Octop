// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

// The browser distribution is the same semantic implementation the extension
// host uses. The relative import keeps this test independent of dashboard
// package resolution; it is a contract fixture, not a production dependency.
// @ts-expect-error sibling SDK package has no dashboard type mapping
import { createDomPerceptionReader } from "../../../../webskill/node_modules/@webskill/sdk/dist/browser.js";

function flatten(nodes: any[]): any[] {
  const result: any[] = [];
  for (const node of nodes) {
    result.push(node);
    if (Array.isArray(node.children)) result.push(...flatten(node.children));
  }
  return result;
}

describe("WebSkill SDK semantic target parity", () => {
  it("produces the same role/name identity Octop expects for native controls", () => {
    document.body.innerHTML = `
      <button title="保存">保存</button>
      <input aria-label="搜索" />
      <a href="/detail">详情</a>
    `;
    const reader = createDomPerceptionReader({
      actionScope: { include: ["body"] },
      promoteRoles: true,
    });
    const nodes = flatten(reader.read({ include: ["body"] }) as any[]);
    expect(
      nodes
        .filter((node) => node.ref !== undefined)
        .map((node) => ({ role: node.role, name: node.name })),
    ).toEqual([
      { role: "button", name: "保存" },
      { role: "textbox", name: "搜索" },
      { role: "link", name: "详情" },
    ]);
  });
});
