import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { seedBundledExpert } from "./seed-bundled-expert";

function writeTemplate(root: string): string {
  const dir = join(root, "template");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "manifest.json"), "{\"id\": \"general-assistant\"}", "utf8");
  writeFileSync(join(dir, "SOUL.md"), "soul", "utf8");
  return dir;
}

test("seeds the bundled expert template into a fresh home", () => {
  const root = mkdtempSync(join(tmpdir(), "octop-seed-expert-"));
  try {
    const home = join(root, "home");
    const template = writeTemplate(root);
    assert.equal(seedBundledExpert(home, template), true);
    assert.equal(
      readFileSync(
        join(home, "expert_market", "general-assistant", "manifest.json"),
        "utf8",
      ),
      "{\"id\": \"general-assistant\"}",
    );
    assert.equal(
      readFileSync(join(home, "expert_market", "general-assistant", "SOUL.md"), "utf8"),
      "soul",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("never overwrites an existing cached template", () => {
  const root = mkdtempSync(join(tmpdir(), "octop-seed-expert-"));
  try {
    const home = join(root, "home");
    const template = writeTemplate(root);
    const dest = join(home, "expert_market", "general-assistant");
    mkdirSync(dest, { recursive: true });
    writeFileSync(join(dest, "manifest.json"), "{\"id\": \"user-installed\"}", "utf8");
    assert.equal(seedBundledExpert(home, template), true);
    assert.equal(readFileSync(join(dest, "manifest.json"), "utf8"), "{\"id\": \"user-installed\"}");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("returns false (no error) when no template is bundled", () => {
  const root = mkdtempSync(join(tmpdir(), "octop-seed-expert-"));
  try {
    const home = join(root, "home");
    assert.equal(seedBundledExpert(home, join(root, "missing")), false);
    assert.equal(existsSync(join(home, "expert_market")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
