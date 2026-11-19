import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  SANDBOX_PAGE_SCRIPT_SOURCE,
  viewerCspHeader,
} from "@webskill/sdk/browser";

const hostOrigin = "https://webskill.invalid";
const contentSecurityPolicy = viewerCspHeader({ hostOrigin });
const publicDir = resolve(import.meta.dirname, "../public");
mkdirSync(resolve(publicDir, "viewer"), { recursive: true });
writeFileSync(
  resolve(publicDir, "viewer/viewer-policy.json"),
  JSON.stringify({ hostOrigin, contentSecurityPolicy }, null, 2),
);
writeFileSync(resolve(publicDir, "sandbox.js"), SANDBOX_PAGE_SCRIPT_SOURCE);
