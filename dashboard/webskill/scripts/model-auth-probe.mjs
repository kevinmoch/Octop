import http from "node:http";
import { createServer as createViteServer } from "vite";

const requests = [];
const authServer = http.createServer((request, response) => {
  if (request.url !== "/v1/chat/completions") {
    response.writeHead(404).end();
    return;
  }
  requests.push({
    authenticated:
      request.headers.authorization === "Bearer expected-test-auth",
  });
  response.writeHead(401, { "content-type": "application/json" });
  response.end(JSON.stringify({ error: "authentication required" }));
});
await new Promise((resolve) => authServer.listen(0, "127.0.0.1", resolve));
const address = authServer.address();
if (!address || typeof address === "string")
  throw new Error("Auth fixture did not bind a TCP port");
const vite = await createViteServer({
  server: { middlewareMode: true },
  logLevel: "silent",
});
const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
  key: () => null,
  get length() {
    return storage.size;
  },
};
try {
  const { createHostLlmClient, LocalNoAuthRegistry } = await vite.ssrLoadModule(
    "/src/model.ts",
  );
  const entry = {
    id: "auth-required",
    label: "Auth-required local fixture",
    provider: "openai-compatible",
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    model: "test-model",
  };
  const config = { llm: { entries: [entry], defaultId: entry.id } };
  const store = {
    load: async () => config,
    save: async () => {},
    reset: async () => {},
    subscribe: () => () => {},
  };
  const registry = new LocalNoAuthRegistry(1);
  registry.set(entry.id, true);
  let code;
  let status;
  try {
    await createHostLlmClient(store, registry).complete({
      messages: [{ role: "user", content: [{ type: "text", text: "test" }] }],
    });
  } catch (error) {
    code = error?.code;
    status = error?.details?.status;
  }
  if (
    code !== "LLM_REQUEST_FAILED" ||
    status !== 401 ||
    requests.length !== 1 ||
    requests[0].authenticated
  ) {
    throw new Error(
      "The authenticated endpoint did not reject the compatibility request exactly once",
    );
  }
  if (JSON.stringify(config).includes("octop-local-no-auth"))
    throw new Error("Compatibility value leaked into configuration");
  console.log(
    JSON.stringify({
      status: "passed",
      endpointRequests: requests.length,
      httpStatus: status,
      errorCode: code,
      placeholderPersisted: false,
    }),
  );
} finally {
  await vite.close();
  await new Promise((resolve) => authServer.close(resolve));
}
