// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import {
  BUILTIN_ROOT,
  seedBuiltinSkills,
  type SkillSeedFs,
} from "./seedSkills";

class MemoryFs implements SkillSeedFs {
  readonly files = new Map<string, string>();
  readonly dirs = new Set<string>();

  async exists(path: string): Promise<boolean> {
    return this.files.has(path) || this.dirs.has(path);
  }

  async readText(path: string): Promise<string> {
    const value = this.files.get(path);
    if (value === undefined) throw new Error(`missing ${path}`);
    return value;
  }

  async writeText(path: string, content: string): Promise<void> {
    const parent = path.slice(0, path.lastIndexOf("/"));
    this.dirs.add(parent);
    this.files.set(path, content);
  }

  async mkdir(path: string): Promise<void> {
    this.dirs.add(path);
  }

  async remove(path: string, options?: { recursive?: boolean }): Promise<void> {
    for (const key of [...this.files.keys()]) {
      if (key === path || (options?.recursive && key.startsWith(`${path}/`))) {
        this.files.delete(key);
      }
    }
    for (const dir of [...this.dirs]) {
      if (dir === path || (options?.recursive && dir.startsWith(`${path}/`))) {
        this.dirs.delete(dir);
      }
    }
  }
}

const ASSETS: Record<string, string> = {
  "authored-bulletin/SKILL.md": "---\nname: authored-bulletin\n---\nbody\n",
  "authored-bulletin/references/authoring.md": "bulletin reference\n",
  "authored-bulletin/scripts/publish.js": "export default 1;\n",
  "authored-screen/SKILL.md": "---\nname: authored-screen\n---\nbody\n",
  "authored-screen/references/authoring.md": "screen reference\n",
  "authored-screen/scripts/publish.js": "export default 2;\n",
  "authored-slides/SKILL.md": "---\nname: authored-slides\n---\nbody\n",
  "authored-slides/references/authoring.md": "slides reference\n",
  "authored-slides/scripts/publish.js": "export default 3;\n",
  "dwg-view/SKILL.md": "---\nname: dwg-view\n---\nbody\n",
};

function resolveAsset(path: string): string {
  const key = path.replace("skills/builtin/", "");
  const content = ASSETS[key];
  if (content === undefined) throw new Error(`no fixture for ${path}`);
  return `data:text/plain;charset=utf-8,${encodeURIComponent(content)}`;
}

describe("seedBuiltinSkills", () => {
  it("writes the four bundled skills into the builtin root", async () => {
    const fs = new MemoryFs();
    expect(await seedBuiltinSkills(fs, resolveAsset)).toBe(true);
    expect([...fs.files.keys()].sort()).toEqual(
      [
        `${BUILTIN_ROOT}/.seed-state.json`,
        ...Object.keys(ASSETS).map((key) => `${BUILTIN_ROOT}/${key}`),
      ].sort(),
    );
    expect(
      fs.files.get(`${BUILTIN_ROOT}/authored-bulletin/SKILL.md`),
    ).toContain("name: authored-bulletin");
  });

  it("is idempotent when the bundled content did not change", async () => {
    const fs = new MemoryFs();
    await seedBuiltinSkills(fs, resolveAsset);
    expect(await seedBuiltinSkills(fs, resolveAsset)).toBe(false);
  });

  it("does not resurrect a skill the user deleted", async () => {
    const fs = new MemoryFs();
    await seedBuiltinSkills(fs, resolveAsset);
    await fs.remove(`${BUILTIN_ROOT}/dwg-view/SKILL.md`);
    await seedBuiltinSkills(fs, resolveAsset);
    expect(await fs.exists(`${BUILTIN_ROOT}/dwg-view/SKILL.md`)).toBe(false);
    await seedBuiltinSkills(fs, resolveAsset);
    expect(await fs.exists(`${BUILTIN_ROOT}/dwg-view`)).toBe(false);
  });

  it("keeps user edits when the bundled file is unchanged", async () => {
    const fs = new MemoryFs();
    await seedBuiltinSkills(fs, resolveAsset);
    await fs.writeText(`${BUILTIN_ROOT}/authored-screen/SKILL.md`, "user edit");
    await seedBuiltinSkills(fs, resolveAsset);
    expect(fs.files.get(`${BUILTIN_ROOT}/authored-screen/SKILL.md`)).toBe(
      "user edit",
    );
  });
});
