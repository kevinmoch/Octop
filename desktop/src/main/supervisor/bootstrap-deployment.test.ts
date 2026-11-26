import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { prepareBootstrapDeployment } from "./bootstrap-deployment";

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, JSON.stringify(value), "utf8");
}

test("deployment splits public bootstrap config from user and model secrets", () => {
  const root = mkdtempSync(join(tmpdir(), "octop-bootstrap-deployment-"));
  try {
    const configFile = join(root, "config.json");
    const userFile = join(root, "users.json");
    const modelFile = join(root, "models.json");
    writeJson(configFile, {
      database: { driver: "sqlite" },
      bootstrap: {
        mcpServers: {
          "welink-mcp-server": {
            transport: "streamable_http",
            url: "http://127.0.0.1:8162/mcp",
          },
        },
      },
    });
    writeJson(userFile, {
      username: "ccs",
      password: "user-secret-for-test",
      display_name: "基建部",
      locale: "zh",
    });
    writeJson(modelFile, {
      name: "DeepSeek",
      type: "openai",
      base_url: "https://api.deepseek.com/v1",
      apiKey: "model-secret-for-test",
      models: [{ id: "deepseek-flash", name: "DeepSeek Flash", context_window: 1048576 }],
    });

    const result = prepareBootstrapDeployment(configFile, userFile, modelFile, join(root, "out"));
    const publicText = readFileSync(result.configFile, "utf8");
    const combined = JSON.parse(publicText);
    assert.equal(combined.bootstrap.enabled, true);
    assert.deepEqual(combined.bootstrap.admin, {
      username: "ccs",
      locale: "zh",
      display_name: "基建部",
    });
    assert.equal(combined.bootstrap.provider.name, "DeepSeek");
    assert.equal(combined.bootstrap.provider.models[0].id, "deepseek-flash");
    assert.equal(combined.bootstrap.provider.models[0].context_window, undefined);
    assert.equal(combined.bootstrap.mcpServers["welink-mcp-server"].url, "http://127.0.0.1:8162/mcp");
    assert.equal(publicText.includes("user-secret-for-test"), false);
    assert.equal(publicText.includes("model-secret-for-test"), false);

    const secrets = JSON.parse(readFileSync(result.secretsFile, "utf8"));
    assert.deepEqual(secrets, {
      admin_password: "user-secret-for-test",
      provider_api_key: "model-secret-for-test",
      mcp_headers: {},
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("deployment rejects missing model credentials instead of silently disabling bootstrap", () => {
  const root = mkdtempSync(join(tmpdir(), "octop-bootstrap-deployment-"));
  try {
    const configFile = join(root, "config.json");
    const userFile = join(root, "users.json");
    const modelFile = join(root, "models.json");
    writeJson(configFile, {});
    writeJson(userFile, { username: "ccs", password: "secret" });
    writeJson(modelFile, { name: "DeepSeek", type: "openai", base_url: "https://api.deepseek.com/v1", models: [{ id: "deepseek-flash", name: "DeepSeek Flash" }] });
    assert.throws(
      () => prepareBootstrapDeployment(configFile, userFile, modelFile, join(root, "out")),
      /model\.apiKey/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
