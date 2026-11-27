// Post-package smoke check: the shipped Dashboard must be the one we just
// built, and it must carry the WebSkill host + builtin skills. Unit tests cover
// React components but cannot catch a stale/missing resources/dashboard copy.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const shellDir = resolve(fileURLToPath(new URL("..", import.meta.url)));
const unpackedByPlatform = {
  win32: join(shellDir, "release", "win-unpacked"),
  darwin: join(shellDir, "release", "mac"),
  linux: join(shellDir, "release", "linux-unpacked"),
};
const target = process.argv[2]
  ? resolve(shellDir, process.argv[2])
  : unpackedByPlatform[process.platform];

const requiredFiles = [
  "resources/dashboard/index.html",
  "resources/dashboard/webskill/index.html",
  "resources/app.asar",
];
const failures = [];
for (const relative of requiredFiles) {
  if (!existsSync(join(target, relative))) failures.push(`missing ${relative}`);
}
// The Windows shell executable has a fixed product name; macOS wraps it in an
// app bundle and Linux uses electron-builder's executable name. Only assert the
// Windows path here and let the shared resources checks cover the other hosts.
if (process.platform === "win32" && !existsSync(join(target, "CCS Buddy.exe"))) {
  failures.push("missing CCS Buddy.exe");
}

const builtinDir = join(target, "resources", "dashboard", "webskill", "skills", "builtin");
const builtinSkills = existsSync(builtinDir)
  ? readdirSync(builtinDir).filter((name) => {
      try {
        return statSync(join(builtinDir, name)).isDirectory();
      } catch {
        return false;
      }
    })
  : [];
const expectedSkills = ["authored-bulletin", "authored-screen", "authored-slides", "dwg-view"];
for (const skill of expectedSkills) {
  if (!builtinSkills.includes(skill)) failures.push(`missing builtin skill ${skill}`);
}

// A stale Dashboard copied from the portable runtime would still pass the
// existence checks above; compare the packaged entry HTML with the build we
// just produced so a release cannot silently ship the old frontend.
const sourceIndex = resolve(shellDir, "../../src/octop/dashboard/index.html");
const packagedIndex = join(target, "resources", "dashboard", "index.html");
if (existsSync(sourceIndex) && existsSync(packagedIndex)) {
  if (!readFileSync(sourceIndex).equals(readFileSync(packagedIndex))) {
    failures.push("resources/dashboard is stale (index.html differs from src/octop/dashboard)");
  }
} else if (!existsSync(sourceIndex)) {
  failures.push("src/octop/dashboard/index.html is missing; build the Dashboard first");
}

if (failures.length > 0) {
  console.error(`[verify-package] ${target}`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`[verify-package] ok ${target} (builtin skills: ${builtinSkills.length})`);
