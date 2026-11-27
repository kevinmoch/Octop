import { promises as fs } from "node:fs";
import { join } from "node:path";

/**
 * Deployment defaults the shell may backfill into an existing user config.
 *
 * Only keys that are missing are added: an operator changing
 * `resources/bootstrap/config.json` should reach machines that already ran the
 * app once, while a value the user put in `~/.octop/config.json` always wins.
 */
const DEPLOYMENT_DEFAULTS = ["desktop_browser_url"] as const;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function backfillDeploymentDefaults(
  configPath: string,
  bootstrap: Record<string, unknown>,
): Promise<boolean> {
  let raw: string;
  try {
    raw = await fs.readFile(configPath, "utf8");
  } catch {
    return false;
  }
  let existing: unknown;
  try {
    existing = JSON.parse(raw);
  } catch {
    // Never rewrite a file we cannot parse: it holds the operator's data.
    return false;
  }
  if (!isPlainObject(existing)) return false;

  let changed = false;
  for (const key of DEPLOYMENT_DEFAULTS) {
    if (key in existing) continue;
    const value = bootstrap[key];
    if (typeof value !== "string" || !value.trim()) continue;
    existing[key] = value.trim();
    changed = true;
  }
  if (!changed) return false;
  await fs.writeFile(configPath, `${JSON.stringify(existing, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  return true;
}

export async function seedBootstrapConfig(
  octopHome: string,
  sourceFile: string,
): Promise<boolean> {
  let raw: string;
  try {
    raw = await fs.readFile(sourceFile, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
  const parsed = JSON.parse(raw) as unknown;
  if (!isPlainObject(parsed)) {
    throw new TypeError("bootstrap config must be a JSON object");
  }
  const document = parsed as Record<string, unknown> & {
    bootstrap?: { enabled?: unknown };
  };
  const enabled = document.bootstrap?.enabled;
  if (enabled !== undefined && typeof enabled !== "boolean") {
    throw new TypeError("bootstrap.enabled must be a boolean");
  }
  if (enabled !== true) return false;

  await fs.mkdir(octopHome, { recursive: true });
  const configPath = join(octopHome, "config.json");
  try {
    await fs.writeFile(configPath, `${JSON.stringify(document, null, 2)}\n`, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  // A user config already exists: first-run seeding is done, but deployment
  // defaults that the operator added later still apply when the key is absent.
  return backfillDeploymentDefaults(configPath, document);
}
