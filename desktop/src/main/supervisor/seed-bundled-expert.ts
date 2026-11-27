import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";

const EXPERT_ID = "general-assistant";

/**
 * Copy `resources/expert-general-assistant/` into `home/expert_market/`.
 *
 * @returns true when the template was seeded (or already present).
 */
export function seedBundledExpert(home: string, bundledDir: string): boolean {
  const manifest = join(bundledDir, "manifest.json");
  if (!existsSync(manifest)) {
    // No bundled template (e.g. a build without OCTOP_DESKTOP_BOOTSTRAP_CONFIG):
    // keep the previous first-run behaviour, including the market download.
    return false;
  }
  const dest = join(home, "expert_market", EXPERT_ID);
  if (existsSync(join(dest, "manifest.json"))) {
    return true;
  }
  rmSync(dest, { recursive: true, force: true });
  mkdirSync(join(home, "expert_market"), { recursive: true });
  cpSync(bundledDir, dest, { recursive: true });
  return true;
}
