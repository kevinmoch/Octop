#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { createRequire } from "node:module";
import { networkInterfaces } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const SDK_ROOT = path.resolve(
  process.env.WEBSKILL_SDK_ROOT ??
    path.join(path.dirname(ROOT), "web-skill-sdk"),
);
const OUT = path.join(ROOT, ".artifacts/webskill-p0");
const DIST = path.join(SDK_ROOT, "packages/sdk/dist");
const MODEL_URL = process.env.OCTOP_P0_MODEL_URL ?? "http://localhost:1234/v1";
const MODEL_NAME = process.env.OCTOP_P0_MODEL_NAME ?? "qwen3.5:9b";
mkdirSync(OUT, { recursive: true });

const sdkRequire = createRequire(path.join(SDK_ROOT, "package.json"));
const { chromium } = sdkRequire("@playwright/test");
const { createServer: createViteServer } = await import(
  pathToFileURL(sdkRequire.resolve("vite")).href
);
const production = await import(
  pathToFileURL(path.join(DIST, "browser.js")).href
);
const { viewerCspHeader } = production;

const results = [];
const safeError = (error) => ({
  name: error instanceof Error ? error.name : typeof error,
  code:
    typeof error === "object" && error !== null && "code" in error
      ? String(error.code)
      : undefined,
  message:
    error instanceof Error
      ? error.message.slice(0, 500)
      : String(error).slice(0, 500),
});
const record = (probe, status, actual, ac, details = {}) => {
  const entry = {
    probe,
    status,
    acStatus:
      status === "failed"
        ? "failed"
        : status === "unsupported"
        ? "gap"
        : "not-assessed",
    actual,
    commands: [
      ".venv/bin/python scripts/webskill-p0/cdp_probe.py",
      "node scripts/webskill-p0/browser_probe.mjs",
    ],
    ac,
    environment: `Chromium ${browser.version()}; SDK 0.29.0; local HTTP/HTTPS`,
    evidence: [".artifacts/webskill-p0/browser-probes.json"],
    ...details,
  };
  results.push(entry);
  console.log(`[${status}] ${probe}: ${actual}`);
};
const listen = (server) =>
  new Promise((resolve) => {
    server.listen(0, "0.0.0.0", () => resolve(server.address().port));
  });
const close = (server) => new Promise((resolve) => server.close(resolve));
const lanAddress = () => {
  if (process.env.OCTOP_P0_LAN_HOST) return process.env.OCTOP_P0_LAN_HOST;
  for (const entries of Object.values(networkInterfaces())) {
    const entry = entries?.find(
      (item) => item.family === "IPv4" && !item.internal,
    );
    if (entry) return entry.address;
  }
  throw new Error("No non-loopback IPv4 interface is available");
};

const probeScript = `
import * as browser from '/sdk/browser.js';
import * as core from '/sdk/index.js';
import * as governance from '/sdk/governance.js';
window.octopP0 = { browser, core, governance };
`;
const viewerScript = `
import { startViewerShell } from '/sdk/browser.js';
const isolation = {};
try { localStorage.setItem('p0', 'forbidden'); isolation.storage = 'available'; }
catch (error) { isolation.storage = error.name; }
try { isolation.parentStorage = window.parent.localStorage ? 'available' : 'unavailable'; }
catch (error) { isolation.parentStorage = error.name; }
isolation.origin = origin;
window.parent.postMessage({ type: 'p0:viewer-boot', openerIsNull: window.opener === null, embedded: window.parent !== window }, '*');
const hostFetch = fetch('/private-fixture').then((response) => ({ status: response.status })).catch((error) => ({ blocked: true, name: error.name }));
const host = new URLSearchParams(location.search).get('embedded') === '1' ? {
  opener: window.parent,
  addEventListener: window.addEventListener.bind(window),
  removeEventListener: window.removeEventListener.bind(window)
} : window;
startViewerShell(host, {
  content: document.getElementById('content'),
  style: document.getElementById('style')
}, async (data) => {
  isolation.hostFetch = await hostFetch;
  window.parent.postMessage({
    type: 'p0:viewer-observation',
    artifact: document.getElementById('artifact')?.textContent,
    isolation,
    untrustedScriptRan: window.untrustedScriptRan === true,
    data
  }, '*');
});
`;
const pageHtml = (script) =>
  `<!doctype html><html><head><meta charset="utf-8"><script type="module" src="${script}"></script></head><body><main>P0</main></body></html>`;
const vite = await createViteServer({
  root: SDK_ROOT,
  configFile: false,
  appType: "custom",
  server: { middlewareMode: true, hmr: false },
});

const createFixtureServer = (httpsOptions) => {
  const handler = (request, response) => {
    const url = new URL(request.url ?? "/", "http://fixture.invalid");
    const origin = `${httpsOptions ? "https" : "http"}://${
      request.headers.host
    }`;
    response.setHeader("X-Content-Type-Options", "nosniff");
    if (url.pathname === "/probe") {
      response.setHeader("Content-Type", "text/html; charset=utf-8");
      response.end(pageHtml("/probe.js"));
      return;
    }
    if (url.pathname === "/probe.js" || url.pathname === "/viewer.js") {
      response.setHeader("Content-Type", "text/javascript; charset=utf-8");
      response.setHeader("Access-Control-Allow-Origin", "*");
      response.end(url.pathname === "/probe.js" ? probeScript : viewerScript);
      return;
    }
    if (url.pathname === "/viewer") {
      response.setHeader("Content-Type", "text/html; charset=utf-8");
      response.setHeader(
        "Content-Security-Policy",
        viewerCspHeader({ hostOrigin: origin }),
      );
      response.end(
        '<!doctype html><html><head><meta charset="utf-8"><style id="style"></style>' +
          '<script type="module" src="/viewer.js"></script></head><body><div id="content"></div></body></html>',
      );
      return;
    }
    if (url.pathname.startsWith("/sdk/")) {
      response.setHeader("Access-Control-Allow-Origin", "*");
      response.setHeader("Cross-Origin-Resource-Policy", "cross-origin");
      request.url = request.url.replace("/sdk/", "/packages/sdk/dist/");
      vite.middlewares(request, response, () => {
        response.statusCode = 404;
        response.end("Not found");
      });
      return;
    }
    if (url.pathname === "/recording-source.json") {
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      response.end(readFileSync(path.join(OUT, "recording-source.json")));
      return;
    }
    if (url.pathname === "/private-fixture") {
      response.setHeader("Content-Type", "application/json");
      response.end('{"fixture":"must-not-be-readable-from-viewer"}');
      return;
    }
    response.setHeader("Access-Control-Allow-Origin", "*");
    response.setHeader("Cross-Origin-Resource-Policy", "cross-origin");
    vite.middlewares(request, response, () => {
      response.statusCode = 404;
      response.end("Not found");
    });
  };
  return httpsOptions
    ? createHttpsServer(httpsOptions, handler)
    : createHttpServer(handler);
};

const certificateDir = path.join(OUT, "tls");
mkdirSync(certificateDir, { recursive: true });
const certPath = path.join(certificateDir, "cert.pem");
const keyPath = path.join(certificateDir, "key.pem");
const host = lanAddress();
const cert = spawnSync(
  "openssl",
  [
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-days",
    "1",
    "-subj",
    `/CN=${host}`,
    "-addext",
    `subjectAltName=IP:${host}`,
    "-keyout",
    keyPath,
    "-out",
    certPath,
  ],
  { stdio: "ignore" },
);
if (cert.status !== 0)
  throw new Error("Could not generate the isolated HTTPS test certificate");

const httpServer = createFixtureServer();
const httpsServer = createFixtureServer({
  cert: readFileSync(certPath),
  key: readFileSync(keyPath),
});
const httpPort = await listen(httpServer);
const httpsPort = await listen(httpsServer);
const origins = {
  localhost: `http://127.0.0.1:${httpPort}`,
  remoteHttp: `http://${host}:${httpPort}`,
  remoteHttps: `https://${host}:${httpsPort}`,
};

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();

async function securityProbe(name, origin) {
  try {
    await page.goto(`${origin}/probe`);
    await page.waitForFunction(() => window.octopP0 !== undefined);
    const observed = await page.evaluate(async () => {
      const { browser, core, governance } = window.octopP0;
      const output = {
        secure: globalThis.isSecureContext,
        opfsDeclared: browser.isOpfsAvailable(),
        webCryptoDeclared: typeof globalThis.crypto?.subtle !== "undefined",
      };
      if (!output.secure || !output.opfsDeclared || !output.webCryptoDeclared)
        return output;
      const fs = new browser.OpfsProvider({
        rootName: `octop-p0-${crypto.randomUUID()}`,
      });
      await fs.writeText("/state/value.txt", "persisted");
      output.opfsRoundTrip =
        (await fs.readText("/state/value.txt")) === "persisted";
      const audit = new governance.FsAuditLog({ root: "/probe", fs });
      await audit.append({
        type: "skill.signature.verified",
        target: "p0-skill",
      });
      output.audit = await audit.verifyChain();
      const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, [
        "sign",
        "verify",
      ]);
      const privateKey = new Uint8Array(
        await crypto.subtle.exportKey("pkcs8", pair.privateKey),
      );
      const publicKey = new Uint8Array(
        await crypto.subtle.exportKey("raw", pair.publicKey),
      );
      const manifest = {
        schemaVersion: 1,
        name: "p0-skill",
        source: { type: "archive", data: new ArrayBuffer(0) },
        installedAt: new Date().toISOString(),
        integrity: { algorithm: "sha256", digest: "a".repeat(64) },
        files: [],
      };
      const signature = await core.signSkill({
        manifest,
        privateKey,
        publicKey,
      });
      const keyId = await core.keyIdOf(publicKey);
      const trustedKeys = {
        get: async (id) =>
          id === keyId
            ? {
                keyId,
                publicKey: signature.publicKey,
                label: "isolated P0",
                addedAt: new Date().toISOString(),
              }
            : undefined,
        list: async () => [],
        add: async () => {},
        remove: async () => {},
      };
      output.signature = await core.verifySkillSignature({
        manifest,
        signature,
        trustedKeys,
        policy: { unsigned: "deny" },
      });
      const tampered = {
        ...signature,
        signature: `${
          signature.signature.startsWith("A") ? "B" : "A"
        }${signature.signature.slice(1)}`,
      };
      try {
        await core.verifySkillSignature({
          manifest,
          signature: tampered,
          trustedKeys,
          policy: { unsigned: "deny" },
        });
        output.tamperRejected = false;
      } catch (error) {
        output.tamperRejected = error.code === "SIGNATURE_MISMATCH";
      }
      return output;
    });
    const primitives = observed.secure
      ? observed.opfsRoundTrip === true &&
        observed.audit?.ok === true &&
        observed.signature?.ok === true &&
        observed.tamperRejected === true
      : observed.opfsDeclared === false || observed.webCryptoDeclared === false;
    record(
      `security-${name}`,
      primitives ? "supported" : "unsupported",
      JSON.stringify(observed),
      ["AC-35"],
      {
        origin,
        hostAdmission: observed.secure
          ? "not implemented"
          : "required explicit refusal is not implemented",
        acStatus: observed.secure ? "not-assessed" : "gap",
      },
    );
  } catch (error) {
    record(
      `security-${name}`,
      "failed",
      "Probe or browser environment failed",
      ["AC-35"],
      {
        origin,
        error: safeError(error),
      },
    );
  }
}

await securityProbe("localhost", origins.localhost);
await securityProbe("remote-http", origins.remoteHttp);
await securityProbe("remote-https", origins.remoteHttps);

try {
  await page.goto(`${origins.localhost}/probe`);
  await page.waitForFunction(() => window.octopP0 !== undefined);
  const model = await page.evaluate(
    async ({ url, name }) => {
      const noAuth = window.octopP0.browser.createLlmClient({
        provider: "openai-compatible",
        baseUrl: url,
        model: name,
      });
      let noAuthError;
      try {
        await noAuth.complete({
          messages: [
            {
              role: "user",
              content: [{ type: "text", text: "Reply with exactly P0_OK." }],
            },
          ],
        });
      } catch (error) {
        noAuthError = { code: error.code, name: error.name };
      }
      const client = window.octopP0.browser.createLlmClient({
        provider: "openai-compatible",
        baseUrl: url,
        model: name,
        apiKey: "p0-test-placeholder",
        requestTimeoutMs: 120000,
      });
      const permission = await navigator.permissions
        ?.query({ name: "loopback-network" })
        .then((value) => value.state)
        .catch((error) => error.name);
      let direct;
      try {
        const response = await fetch(`${url}/models`, {
          headers: { authorization: "Bearer " },
        });
        direct = { status: response.status, ok: response.ok };
      } catch (error) {
        direct = { blocked: true, name: error.name, message: error.message };
      }
      const availability = await client.checkAvailability();
      try {
        const reply = await client.complete({
          messages: [
            {
              role: "user",
              content: [{ type: "text", text: "Reply with exactly P0_OK." }],
            },
          ],
          temperature: 0,
        });
        const content = (reply.content ?? [])
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join("");
        return {
          availability,
          permission,
          direct,
          noAuthError,
          contentLength: content.length,
          acknowledged: content.includes("P0_OK"),
          usage: reply.usage,
        };
      } catch (error) {
        return {
          availability,
          permission,
          direct,
          noAuthError,
          completionError: {
            name: error.name,
            code: error.code,
            message: error.message?.slice(0, 500),
          },
        };
      }
    },
    { url: MODEL_URL, name: MODEL_NAME },
  );
  record(
    "model-cors-and-real-completion",
    model.acknowledged ? "supported" : "failed",
    model.acknowledged
      ? "The production browser client completed against the configured real model"
      : "The real model did not complete as requested",
    ["AC-29", "AC-49"],
    {
      modelOrigin: new URL(MODEL_URL).origin,
      modelName: MODEL_NAME,
      observation: model,
    },
  );
  record(
    "model-no-auth-configuration",
    model.noAuthError?.code === "LLM_UNAVAILABLE" ? "unsupported" : "supported",
    model.noAuthError?.code === "LLM_UNAVAILABLE"
      ? "The current OpenAI-compatible client requires a non-empty apiKey even when the real local service needs no authentication"
      : "The production client accepted the no-auth configuration",
    ["AC-29", "AC-49"],
    { observation: model.noAuthError },
  );
  record(
    "model-lna-permission-query",
    ["prompt", "granted", "denied"].includes(model.permission)
      ? "supported"
      : "unsupported",
    "The real navigator.permissions query returned a loopback-network state; no denial-path claim is made",
    ["AC-49"],
    {
      permission: model.permission,
      denialPathVerified: false,
      acStatus: "not-assessed",
    },
  );

  await page.goto(`${origins.remoteHttps}/probe`);
  const httpsToHttp = await page.evaluate(async (url) => {
    try {
      const response = await fetch(`${url}/models`);
      return { status: response.status, readable: response.ok };
    } catch (error) {
      return {
        blocked: true,
        error: { name: error.name, message: error.message },
      };
    }
  }, MODEL_URL);
  record(
    "https-dashboard-to-http-model",
    httpsToHttp.blocked ? "unsupported" : "supported",
    httpsToHttp.blocked
      ? "Plain HTTP model endpoints are not a valid default for an HTTPS dashboard; no fallback was used"
      : "The browser allowed the configured cross-origin model request",
    ["AC-49"],
    { observation: httpsToHttp },
  );
} catch (error) {
  record(
    "model-cors-and-real-completion",
    "failed",
    "Real model service or browser model probe failed",
    ["AC-29", "AC-49"],
    { error: safeError(error) },
  );
}

try {
  await page.goto(`${origins.localhost}/probe`);
  const response = await page.request.get(`${origins.localhost}/viewer`);
  const csp = response.headers()["content-security-policy"] ?? "";
  const viewer = await page.evaluate(async () => {
    const { browser } = window.octopP0;
    let frame;
    const observations = [];
    const boots = [];
    const auditEvents = [];
    const onObservation = (event) => {
      if (event.source !== frame?.contentWindow) return;
      if (event.data?.type === "p0:viewer-observation")
        observations.push(event.data);
      if (event.data?.type === "p0:viewer-boot") boots.push(event.data);
    };
    window.addEventListener("message", onObservation);
    let target;
    try {
      target = await browser.openDocumentSurface(
        {
          skillName: "p0-viewer",
          dataSource: "fixed-fixture",
          document: {
            html: '<h1 id="artifact">P0 artifact</h1><script>window.untrustedScriptRan = true</script>',
            data: { safe: true },
          },
        },
        {
          viewerUrl: "/viewer",
          confirm: async () => true,
          handshakeTimeoutMs: 5000,
          open: () => {
            frame = document.createElement("iframe");
            frame.src = "/viewer";
            document.body.append(frame);
            return frame.contentWindow;
          },
          audit: {
            append: async (event) => {
              auditEvents.push(event);
            },
          },
        },
      );
    } catch (error) {
      window.removeEventListener("message", onObservation);
      return {
        handshakeFailed: true,
        code: error.code,
        viewerBoot: boots[0],
        closeLeavesHostCleanupToOwner: frame?.isConnected,
        auditOutcomes: auditEvents.map((event) => event.data?.outcome),
      };
    }
    for (let i = 0; i < 250 && observations.length === 0; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    window.removeEventListener("message", onObservation);
    const observation = observations[0];
    if (!observation)
      throw new Error("Viewer did not report its actual isolated state");
    const frameWindow = frame.contentWindow;
    target.close();
    return {
      realContentWindow: target === frameWindow,
      handshakeAndRender: observation.artifact === "P0 artifact",
      isolation: observation.isolation,
      untrustedScriptRan: observation.untrustedScriptRan,
      dataDelivered: observation.data?.safe === true,
      auditOutcomes: auditEvents.map((event) => event.data?.outcome),
      closeLeavesHostCleanupToOwner: frame.isConnected,
      viewerOrigin: observation.isolation.origin,
    };
  });
  const isolated =
    viewer.isolation?.storage === "SecurityError" &&
    viewer.isolation?.parentStorage === "SecurityError" &&
    viewer.viewerOrigin === "null" &&
    viewer.isolation?.hostFetch?.blocked === true;
  const viewerStatus = viewer.handshakeFailed
    ? viewer.viewerBoot?.openerIsNull
      ? "unsupported"
      : "failed"
    : viewer.realContentWindow &&
      viewer.handshakeAndRender &&
      isolated &&
      !viewer.untrustedScriptRan &&
      viewer.dataDelivered
    ? "supported"
    : "unsupported";
  record(
    "viewer-http-sandbox-csp-and-handshake",
    viewerStatus,
    viewer.handshakeFailed
      ? "The viewer booted but did not complete the real iframe handshake; the observed opener/embedding facts are preserved"
      : "Real iframe contentWindow completed the SDK handshake; storage isolation and script behavior were observed in Chromium",
    ["AC-27", "AC-28"],
    {
      csp,
      sandboxTokens: production.VIEWER_SANDBOX_TOKENS,
      moduleCors: "required and supplied by the fixture",
      observation: viewer,
    },
  );
  record(
    "viewer-close-lifecycle",
    viewer.closeLeavesHostCleanupToOwner === undefined
      ? "failed"
      : viewer.closeLeavesHostCleanupToOwner
      ? "unsupported"
      : "supported",
    viewer.closeLeavesHostCleanupToOwner
      ? "Window.close does not remove a host-owned iframe; internal embedding needs an explicit host lifecycle/dispose contract"
      : "The host-owned viewer was removed",
    ["AC-28"],
  );
} catch (error) {
  record(
    "viewer-http-sandbox-csp-and-handshake",
    "failed",
    "Real viewer probe failed",
    ["AC-27", "AC-28"],
    {
      error: safeError(error),
    },
  );
}

try {
  await page.goto(`${origins.localhost}/probe`);
  const adapted = await page.evaluate(async () => {
    const { browser } = window.octopP0;
    let frame;
    let observation;
    const onMessage = (event) => {
      if (
        event.source === frame?.contentWindow &&
        event.data?.type === "p0:viewer-observation"
      )
        observation = event.data;
    };
    window.addEventListener("message", onMessage);
    const target = await browser.openDocumentSurface(
      {
        skillName: "p0-viewer",
        dataSource: "fixed-fixture",
        document: {
          html: '<h1 id="artifact">P0 artifact</h1><script>window.untrustedScriptRan = true</script>',
          data: { safe: true },
        },
      },
      {
        viewerUrl: "/viewer?embedded=1",
        confirm: async () => true,
        handshakeTimeoutMs: 5000,
        open: () => {
          frame = document.createElement("iframe");
          frame.src = "/viewer?embedded=1";
          document.body.append(frame);
          return frame.contentWindow;
        },
      },
    );
    for (let i = 0; i < 250 && !observation; i += 1)
      await new Promise((resolve) => setTimeout(resolve, 20));
    window.removeEventListener("message", onMessage);
    const result = {
      realContentWindow: target === frame?.contentWindow,
      rendered: observation?.artifact === "P0 artifact",
      isolation: observation?.isolation,
      untrustedScriptRan: observation?.untrustedScriptRan,
      dataDelivered: observation?.data?.safe === true,
    };
    frame?.remove();
    return result;
  });
  const valid =
    adapted.realContentWindow &&
    adapted.rendered &&
    adapted.dataDelivered &&
    !adapted.untrustedScriptRan &&
    adapted.isolation?.storage === "SecurityError" &&
    adapted.isolation?.parentStorage === "SecurityError" &&
    adapted.isolation?.hostFetch?.blocked === true;
  record(
    "viewer-real-parent-host-adapter",
    valid ? "supported" : "unsupported",
    "The existing ViewerShellHost port was bound to the real window.parent while openDocumentSurface received the real iframe contentWindow; no SDK or public contract was changed",
    ["AC-27", "AC-28"],
    { observation: adapted },
  );
} catch (error) {
  record(
    "viewer-real-parent-host-adapter",
    "failed",
    "The real-parent host adaptation probe failed",
    ["AC-27", "AC-28"],
    { error: safeError(error) },
  );
}

try {
  await page.goto(`${origins.localhost}/probe`);
  const candidate = await page.evaluate(async () => {
    const source = await fetch("/recording-source.json").then((response) =>
      response.json(),
    );
    const { browser, core, governance } = window.octopP0;
    const fs = new browser.OpfsProvider({
      rootName: `octop-p0-candidates-${crypto.randomUUID()}`,
    });
    const store = new governance.CandidateStore({ root: "/governance", fs });
    const sink = governance.createCandidateSink({ store });
    const raw = source.content;
    const parsed = core.parseSkillMarkdown(raw);
    const draft = {
      name: "recorded-p0",
      description: "Review a fixed local browser workflow.",
      content: raw,
    };
    const canonicalize = (value) => {
      if (
        value === null ||
        typeof value === "string" ||
        typeof value === "boolean"
      )
        return value;
      if (typeof value === "number" && Number.isFinite(value)) return value;
      if (Array.isArray(value)) return value.map(canonicalize);
      if (
        typeof value === "object" &&
        value !== null &&
        (Object.getPrototypeOf(value) === Object.prototype ||
          Object.getPrototypeOf(value) === null)
      ) {
        return Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((key) => [key, canonicalize(value[key])]),
        );
      }
      throw new Error("Draft contains a non-JSON value");
    };
    const rejectUnknown = (value, allowed, where) => {
      const unknown = Object.keys(value).filter(
        (key) => !allowed.includes(key),
      );
      if (unknown.length > 0)
        throw new Error(
          `${where} contains unknown fields: ${unknown.join(",")}`,
        );
    };
    const canonicalDraft = (input) => {
      rejectUnknown(
        input,
        ["name", "description", "content", "files"],
        "Draft",
      );
      return canonicalize({
        name: input.name,
        description: input.description,
        content: input.content,
        ...(input.files === undefined
          ? {}
          : {
              files: input.files.map((file) => {
                rejectUnknown(file, ["path", "content"], "Draft file");
                return { path: file.path, content: file.content };
              }),
            }),
      });
    };
    const normalizedDraft = canonicalDraft(draft);
    const digest = await browser.sha256HexWeb(JSON.stringify(normalizedDraft));
    const reordered = {
      content: draft.content,
      name: draft.name,
      description: draft.description,
    };
    const canonicalKeyOrderMatches =
      digest ===
      (await browser.sha256HexWeb(JSON.stringify(canonicalDraft(reordered))));
    const mutationRejected =
      digest !==
      (await browser.sha256HexWeb(
        JSON.stringify(canonicalDraft({ ...draft, description: "changed" })),
      ));
    let unknownFieldRejected = false;
    try {
      canonicalDraft({ ...draft, steps: [] });
    } catch {
      unknownFieldRejected = true;
    }
    const finiteNumbersAccepted = canonicalize({ rank: 1 }).rank === 1;
    let nonfiniteRejected = false;
    try {
      canonicalize({ rank: Number.NaN });
    } catch {
      nonfiniteRejected = true;
    }
    const submitted = await sink.submit({
      draft: normalizedDraft,
      confirmedAt: new Date().toISOString(),
    });
    const persisted = await store.get(submitted.id);
    return {
      noSteps: !("steps" in normalizedDraft),
      canonicalKeyOrderMatches,
      mutationRejected,
      unknownFieldRejected,
      finiteNumbersAccepted,
      nonfiniteRejected,
      sessionIdOmitted: persisted.metadata?.sessionId === undefined,
      candidateId: persisted.id,
      candidateStatus: persisted.status,
      source: persisted.source,
      declaredAllowedTools: parsed.metadata["allowed-tools"],
      sameDeterministicTemplate: raw === source.secondContent,
      sdkHasDraftValidator:
        typeof governance.validateGeneratedSkillDraft === "function",
      endpointImplemented: false,
    };
  });
  const feasible =
    candidate.noSteps &&
    candidate.canonicalKeyOrderMatches &&
    candidate.mutationRejected &&
    candidate.unknownFieldRejected &&
    candidate.finiteNumbersAccepted &&
    candidate.nonfiniteRejected &&
    candidate.sessionIdOmitted &&
    candidate.candidateStatus === "draft" &&
    candidate.sameDeterministicTemplate;
  record(
    "candidate-sink-primitive",
    feasible ? "supported" : "unsupported",
    "Only the sink and canonicalization test vector were exercised: key order is irrelevant, mutations and unknown fields are rejected, finite numbers survive, non-finite numbers fail, and the submitted normalized object matches the digest input. This does not accept the Bash SKILL.md as a WebSkill candidate",
    [],
    { observation: candidate, acStatus: "not-applicable" },
  );
  record(
    "candidate-adapter",
    candidate.declaredAllowedTools === "Bash" || !candidate.sdkHasDraftValidator
      ? "unsupported"
      : "supported",
    "The real fixed WebSkill template adapter, strict untrusted-input validation and cross-endpoint draftDigest flow are not implemented; raw octop-browser Bash content must not be submitted unchanged",
    ["AC-22", "AC-53", "AC-54", "AC-55"],
  );
} catch (error) {
  record(
    "candidate-sink-primitive",
    "failed",
    "Candidate sink probe failed; run the Python CDP probe first",
    [],
    { error: safeError(error) },
  );
}

writeFileSync(
  path.join(OUT, "browser-probes.json"),
  JSON.stringify(
    { generatedAt: new Date().toISOString(), origins, results },
    null,
    2,
  ),
);
await context.close();
await browser.close();
await close(httpServer);
await close(httpsServer);
await vite.close();
console.log(`Evidence: ${path.join(OUT, "browser-probes.json")}`);
if (results.some((result) => result.status === "failed")) process.exitCode = 1;
