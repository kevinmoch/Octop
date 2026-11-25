import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { seedBootstrapConfig } from "./bootstrap-config";

async function fixture() {
  const root = await fs.mkdtemp(join(tmpdir(), "octop-bootstrap-config-"));
  const home = join(root, "home");
  const source = join(root, "bootstrap.json");
  return {
    root,
    home,
    source,
    cleanup: () => fs.rm(root, { recursive: true, force: true }),
  };
}

test("enabled bootstrap config is seeded only when no user config exists", async () => {
  const fx = await fixture();
  const config = {
    database: { driver: "sqlite" },
    bootstrap: { enabled: true },
  };
  await fs.writeFile(fx.source, JSON.stringify(config), "utf8");
  assert.equal(await seedBootstrapConfig(fx.home, fx.source), true);
  assert.deepEqual(
    JSON.parse(await fs.readFile(join(fx.home, "config.json"), "utf8")),
    config,
  );

  const replacement = {
    bootstrap: { enabled: true, admin: { username: "other" } },
  };
  await fs.writeFile(fx.source, JSON.stringify(replacement), "utf8");
  assert.equal(await seedBootstrapConfig(fx.home, fx.source), false);
  assert.deepEqual(
    JSON.parse(await fs.readFile(join(fx.home, "config.json"), "utf8")),
    config,
  );
  await fx.cleanup();
});

test("disabled or absent bootstrap config leaves first-run setup unchanged", async () => {
  const fx = await fixture();
  await fs.writeFile(
    fx.source,
    JSON.stringify({ bootstrap: { enabled: false } }),
    "utf8",
  );
  assert.equal(await seedBootstrapConfig(fx.home, fx.source), false);
  assert.equal(
    await seedBootstrapConfig(fx.home, join(fx.root, "missing")),
    false,
  );
  await assert.rejects(fs.readFile(join(fx.home, "config.json"), "utf8"));
  await fx.cleanup();
});

test("invalid bootstrap config fails before touching user data", async () => {
  const fx = await fixture();
  await fs.writeFile(fx.source, "{not-json", "utf8");
  await assert.rejects(seedBootstrapConfig(fx.home, fx.source), SyntaxError);
  await fs.writeFile(
    fx.source,
    JSON.stringify({ bootstrap: { enabled: "yes" } }),
    "utf8",
  );
  await assert.rejects(seedBootstrapConfig(fx.home, fx.source), /enabled/);
  await fx.cleanup();
});
