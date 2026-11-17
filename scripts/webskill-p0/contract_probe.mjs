#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const SDK_ROOT = path.resolve(
  process.env.WEBSKILL_SDK_ROOT ??
    path.join(path.dirname(ROOT), "web-skill-sdk"),
);
const OUT = path.join(ROOT, ".artifacts/webskill-p0");
mkdirSync(OUT, { recursive: true });

const run = (command, args, cwd = ROOT) =>
  spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    timeout: 180_000,
  });
const before = run("git", ["status", "--porcelain"], SDK_ROOT);
const build = run("pnpm", ["build"], SDK_ROOT);
const api = build.status === 0 ? run("pnpm", ["api:check"], SDK_ROOT) : null;
const after = run("git", ["status", "--porcelain"], SDK_ROOT);
const releaseHistory = readFileSync(
  path.join(SDK_ROOT, "RELEASING.md"),
  "utf8",
);
const documented =
  releaseHistory.includes("The 0.28.0 tag exists") &&
  releaseHistory.includes("but was not published");
const versionAbsent = (version) => {
  const response = run(
    "npm",
    ["view", `@webskill/sdk@${version}`, "version", "--prefer-online"],
    SDK_ROOT,
  );
  return (
    response.status === 1 &&
    response.stdout.trim() === "" &&
    /E404|404/.test(response.stderr)
  );
};
const unpublished = versionAbsent("0.28.0");
const supported =
  before.status === 0 &&
  build.status === 0 &&
  api?.status === 0 &&
  after.status === 0 &&
  after.stdout === before.stdout &&
  documented &&
  unpublished;
const result = {
  probe: "api-snapshot-and-version-discipline",
  status: supported ? "supported" : "failed",
  acStatus: "not-assessed",
  actual: supported
    ? "Local SDK build and API snapshot passed with no worktree change; the documented unpublished 0.28.0 remains absent. No version is allocated, nothing is published, and CI was not run"
    : "The local build/API/history/registry probe did not produce complete passing evidence",
  commands: [
    "pnpm build",
    "pnpm api:check",
    "git status --porcelain",
    "npm view @webskill/sdk@0.28.0 version --prefer-online",
    "node scripts/webskill-p0/contract_probe.mjs",
  ],
  ac: ["AC-48"],
  environment: `SDK checkout ${SDK_ROOT}; Node ${process.version}`,
  evidence: [".artifacts/webskill-p0/contract-probes.json"],
  buildExitCode: build.status,
  apiExitCode: api?.status,
  worktreeUnchanged: after.stdout === before.stdout,
  versionDispositionsDocumented: documented,
  unpublishedVersionAbsent: unpublished,
  ciVerified: false,
};
writeFileSync(
  path.join(OUT, "contract-probes.json"),
  JSON.stringify(
    { generatedAt: new Date().toISOString(), results: [result] },
    null,
    2,
  ),
);
console.log(`[${result.status}] ${result.probe}: ${result.actual}`);
console.log(`Evidence: ${path.join(OUT, "contract-probes.json")}`);
if (!supported) process.exitCode = 1;
