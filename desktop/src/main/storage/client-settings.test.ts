import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { ClientSettingsStore } from "./client-settings";

function withStore(run: (store: ClientSettingsStore, path: string) => void): void {
  const root = mkdtempSync(join(tmpdir(), "octop-client-settings-"));
  try {
    const path = join(root, "desktop-settings.json");
    run(new ClientSettingsStore(path), path);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("browser mode defaults to local", () => {
  withStore((store) => {
    assert.equal(store.load().browserMode, "local");
  });
});

test("browser mode persists across loads", () => {
  withStore((store, path) => {
    const saved = store.save({ browserMode: "remote" });
    assert.equal(saved.browserMode, "remote");
    assert.equal(store.load().browserMode, "remote");
    assert.equal(JSON.parse(readFileSync(path, "utf8")).browserMode, "remote");
  });
});

test("an invalid stored browser mode falls back to local", () => {
  withStore((store, path) => {
    writeFileSync(path, JSON.stringify({ browserMode: "sideways" }), "utf8");
    assert.equal(store.load().browserMode, "local");
  });
});

test("an older settings file without browser mode still loads", () => {
  withStore((store, path) => {
    writeFileSync(path, JSON.stringify({ closeBehavior: "quit" }), "utf8");
    const settings = store.load();
    assert.equal(settings.closeBehavior, "quit");
    assert.equal(settings.browserMode, "local");
  });
});
