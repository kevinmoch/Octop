// Windows development launcher: prepare the runtime, build the shell, then
// start Electron with Dashboard hot reload. Equivalent to run-dev.sh on Unix.
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const shellDir = resolve(scriptDir, "..");
const repoRoot = resolve(shellDir, "../..");
const devRuntime = join(shellDir, ".dev-runtime");
const dashboardDir = join(repoRoot, "src", "octop", "dashboard");
const electronBin = join(shellDir, "node_modules", "electron", "dist", "electron.exe");

const args = process.argv.slice(2);
const noBuild = args.includes("--no-build");
const extraArgs = args.includes("--") ? args.slice(args.indexOf("--") + 1) : [];

function run(command, commandArgs, options = {}) {
  const result = spawnSync(command, commandArgs, {
    stdio: "inherit",
    // Do not use a shell: `process.execPath` often contains spaces
    // ("...\Author Software\nvm\...") and cmd would split it. Every command
    // here is an absolute .exe path, so direct spawning is both safer and
    // independent of bash/cmd PATH differences.
    shell: false,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (!existsSync(electronBin)) {
  console.error(`missing ${electronBin} — install Electron first`);
  process.exit(1);
}

if (!noBuild) {
  run(process.execPath, [join(scriptDir, "make-dev-runtime.mjs")]);
  console.log("[dev] building electron shell…");
  // Use the local electron-vite entry instead of `pnpm run build`: pnpm may be
  // missing from PATH in Git Bash / minimal shells, and spawning it indirectly
  // is what produces "bash: pnpm: command not found" on Windows dev machines.
  run(
    process.execPath,
    [join(shellDir, "node_modules", "electron-vite", "bin", "electron-vite.js"), "build"],
    { cwd: shellDir },
  );
}

if (!existsSync(join(shellDir, "out", "main", "index.js"))) {
  console.error("out/main/index.js missing — run 'pnpm build' first (or drop --no-build)");
  process.exit(1);
}
if (!existsSync(join(devRuntime, "runtime", "python.exe"))) {
  console.error(`${devRuntime} missing — run 'node scripts/make-dev-runtime.mjs' first (or drop --no-build)`);
  process.exit(1);
}

const env = {
  ...process.env,
  OCTOP_PORTABLE_DIR: devRuntime,
  OCTOP_DASHBOARD_DIR: dashboardDir,
  OCTOP_DASHBOARD_DEV: "1",
  OCTOP_DASHBOARD_SOURCE: join(repoRoot, "dashboard"),
  OCTOP_DASHBOARD_DEV_PORT: process.env.OCTOP_DASHBOARD_DEV_PORT ?? "5173",
};
delete env.ELECTRON_RUN_AS_NODE;

console.log("[dev] launching (loopback only; dashboard via Vite)…");
const startedAt = Date.now();
const child = spawn(electronBin, [shellDir, ...extraArgs], {
  stdio: "inherit",
  env,
  shell: false,
});
child.on("exit", (code) => {
  if (code === 0 && Date.now() - startedAt < 8_000) {
    console.warn(
      "[dev] Electron exited immediately. If CCS Buddy is already running, " +
        "quit it from the system tray and retry; the dev shell shares its " +
        "single-instance lock.",
    );
  }
  process.exit(code ?? 0);
});
