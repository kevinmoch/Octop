import { promises as fs } from "node:fs";
import { join } from "node:path";

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
  const parsed = JSON.parse(raw) as {
    bootstrap?: { enabled?: unknown };
  };
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new TypeError("bootstrap config must be a JSON object");
  }
  const enabled = parsed.bootstrap?.enabled;
  if (enabled !== undefined && typeof enabled !== "boolean") {
    throw new TypeError("bootstrap.enabled must be a boolean");
  }
  if (enabled !== true) return false;

  await fs.mkdir(octopHome, { recursive: true });
  try {
    await fs.writeFile(
      join(octopHome, "config.json"),
      `${JSON.stringify(parsed, null, 2)}\n`,
      {
        encoding: "utf8",
        flag: "wx",
        mode: 0o600,
      },
    );
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") return false;
    throw error;
  }
  return true;
}
