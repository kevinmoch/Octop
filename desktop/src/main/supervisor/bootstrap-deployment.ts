import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

type JsonObject = Record<string, unknown>;

export interface BootstrapDeploymentResources {
  configFile: string;
  secretsFile: string;
}

/**
 * Pick the brand build's bootstrap source.
 *
 * An explicit `OCTOP_DESKTOP_BOOTSTRAP_CONFIG` always wins (CI releases use
 * it). Otherwise a checkout that has the gitignored `users.json` and
 * `models.json` next to `resources/bootstrap/config.json` is a brand build:
 * `pnpm package` picks those up without requiring extra environment setup.
 * Plain deployments keep packaging the disabled `resources/bootstrap.json`
 * template.
 */
export function resolveBootstrapDeploymentConfig(
  bootstrapDir: string,
  envValue: string | undefined,
): string | null {
  const explicit = (envValue ?? "").trim();
  if (explicit) return explicit;
  const config = join(bootstrapDir, "config.json");
  if (!existsSync(config)) return null;
  if (!existsSync(join(bootstrapDir, "users.json"))) return null;
  if (!existsSync(join(bootstrapDir, "models.json"))) return null;
  return config;
}

function readObject(file: string): JsonObject {
  const value = JSON.parse(readFileSync(file, "utf8")) as unknown;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${file} must contain a JSON object`);
  }
  return value as JsonObject;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new TypeError(`${field} must be a non-empty string`);
  }
  return value;
}

export function prepareBootstrapDeployment(
  configFile: string,
  userFile: string,
  modelFile: string,
  outDir: string,
): BootstrapDeploymentResources {
  const base = readObject(configFile);
  const user = readObject(userFile);
  const model = readObject(modelFile);
  const baseBootstrap =
    typeof base.bootstrap === "object" && base.bootstrap !== null && !Array.isArray(base.bootstrap)
      ? (base.bootstrap as JsonObject)
      : {};
  const models = model.models;
  if (!Array.isArray(models) || models.length === 0) {
    throw new TypeError("model.models must be a non-empty array");
  }
  const normalizedModels = models.map((entry, index) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new TypeError(`model.models.${index} must be an object`);
    }
    const normalized: JsonObject = {
      id: requiredString(entry.id, `model.models.${index}.id`),
      name: requiredString(entry.name, `model.models.${index}.name`),
    };
    for (const key of ["enabled", "input", "thinking", "reasoning"] as const) {
      if (key in entry) normalized[key] = entry[key];
    }
    return normalized;
  });
  const admin: JsonObject = {
    username: requiredString(user.username, "user.username"),
    locale: user.locale === "en" ? "en" : "zh",
  };
  if (typeof user.display_name === "string") admin.display_name = user.display_name;
  if (typeof user.email === "string") admin.email = user.email;
  const provider: JsonObject = {
    name: requiredString(model.name, "model.name"),
    type: requiredString(model.type, "model.type"),
    base_url: requiredString(model.base_url, "model.base_url"),
    models: normalizedModels,
  };
  if (typeof model.extras === "object" && model.extras !== null && !Array.isArray(model.extras)) {
    provider.extras = model.extras;
  }
  const combined: JsonObject = {
    ...base,
    database: base.database ?? { driver: "sqlite" },
    bootstrap: {
      ...baseBootstrap,
      enabled: true,
      admin,
      provider,
    },
  };
  const secrets = {
    admin_password: requiredString(user.password, "user.password"),
    provider_api_key: requiredString(model.apiKey, "model.apiKey"),
    mcp_headers:
      typeof user.mcp_headers === "object" && user.mcp_headers !== null && !Array.isArray(user.mcp_headers)
        ? user.mcp_headers
        : {},
  };
  mkdirSync(outDir, { recursive: true });
  const combinedFile = join(outDir, "bootstrap.json");
  const secretsFile = join(outDir, "bootstrap.secrets.json");
  writeFileSync(combinedFile, `${JSON.stringify(combined, null, 2)}\n`, "utf8");
  writeFileSync(secretsFile, `${JSON.stringify(secrets, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  return { configFile: combinedFile, secretsFile };
}
