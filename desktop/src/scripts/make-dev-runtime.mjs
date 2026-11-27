// Prepare a Windows dev portable runtime without the Unix shell scripts.
// The shell spawns `<dir>/runtime/python.exe <dir>/launch.py run`, so we
// materialize a small wrapper around the repo's uv venv.
import {
  existsSync,
  mkdirSync,
  copyFileSync,
  writeFileSync,
  readFileSync,
  statSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const shellDir = resolve(scriptDir, "..");
const repoRoot = resolve(shellDir, "../..");
const venv = join(repoRoot, ".venv");
const dev = join(shellDir, ".dev-runtime");

const venvPython = join(venv, "Scripts", "python.exe");
if (!existsSync(venvPython)) {
  console.error(`missing ${venvPython} — run 'uv sync' in ${repoRoot} first`);
  process.exit(1);
}

mkdirSync(join(dev, "runtime"), { recursive: true });
mkdirSync(join(dev, "packages"), { recursive: true });

/**
 * Copy a runtime file, but keep the existing copy when Windows says it is in
 * use. This happens when the desktop app is already running: its Python
 * sidecar locks `.dev-runtime/runtime/python.exe`, and the dev launcher must
 * still be able to prepare the rest of the tree.
 */
function copyRuntimeFile(source, target) {
  if (!existsSync(source)) return;
  if (existsSync(target)) {
    try {
      const src = statSync(source);
      const dst = statSync(target);
      if (src.size === dst.size && src.mtimeMs <= dst.mtimeMs) return;
    } catch {
      /* fall through to the copy attempt */
    }
  }
  try {
    copyFileSync(source, target);
  } catch (error) {
    if (
      (error.code === "EBUSY" || error.code === "EPERM") &&
      existsSync(target)
    ) {
      console.warn(`[dev] ${target} is in use; reusing the existing copy`);
      return;
    }
    throw error;
  }
}

copyRuntimeFile(venvPython, join(dev, "runtime", "python.exe"));
const venvPythonW = join(venv, "Scripts", "pythonw.exe");
if (existsSync(venvPythonW)) {
  copyRuntimeFile(venvPythonW, join(dev, "runtime", "pythonw.exe"));
}

// The copied interpreter discovers the real venv through pyvenv.cfg.
const venvCfg = readFileSync(join(venv, "pyvenv.cfg"), "utf8");
writeFileSync(join(dev, "runtime", "pyvenv.cfg"), venvCfg);

copyRuntimeFile(
  join(repoRoot, "desktop", "portable", "templates", "launch.py"),
  join(dev, "launch.py"),
);

// launch.py processes packages/*.pth via site.addsitedir. A nested addsitedir
// makes the repo venv's editable installs (octop, octop-harness, …) visible.
writeFileSync(
  join(dev, "packages", "venv.pth"),
  `import site; site.addsitedir(r'${join(venv, "Lib", "site-packages").replace(/\\/g, "\\\\")}')\n`,
);

const pyproject = readFileSync(join(repoRoot, "pyproject.toml"), "utf8");
const version = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1] ?? "0.0.0-dev";
writeFileSync(join(dev, "version.json"), JSON.stringify({ version }, null, 2));

console.log(`dev runtime ready: ${dev}`);
console.log(`set OCTOP_PORTABLE_DIR=${dev}`);
console.log(`set OCTOP_DASHBOARD_DIR=${join(repoRoot, "src", "octop", "dashboard")}`);
