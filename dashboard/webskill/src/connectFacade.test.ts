// @vitest-environment node
import { createServer } from "node:http";
import type { Server } from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { JSDOM } from "jsdom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHostConnectFacade } from "./connectFacade";
import { createDownloadConsent } from "./downloadConsent";
import { createPageActionConsent } from "./pageActionConsent";

function makeMcpServer(exposeEcho: boolean): McpServer {
  const mcp = new McpServer({ name: "custom-e2e", version: "1.0.0" });
  if (exposeEcho) {
    mcp.registerTool(
      "custom_echo",
      { description: "Echo from a user-configured MCP service" },
      async () => ({
        content: [{ type: "text", text: "custom-service-called" }],
      }),
    );
  }
  return mcp;
}

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();
  get length() {
    return this.values.size;
  }
  clear() {
    this.values.clear();
  }
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  setItem(key: string, value: string) {
    this.values.set(key, String(value));
  }
}

describe("custom MCP endpoints", () => {
  let server: Server;
  let mcpUrl: string;
  let exposeEcho = true;
  let facade: ReturnType<typeof createHostConnectFacade> | undefined;

  beforeEach(async () => {
    exposeEcho = true;
    Object.defineProperty(globalThis, "document", {
      value: new JSDOM("").window.document,
      configurable: true,
    });
    Object.defineProperty(globalThis, "localStorage", {
      value: new MemoryStorage(),
      configurable: true,
    });
    localStorage.clear();
    server = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (chunk: Buffer) => chunks.push(chunk));
      req.on("end", () => {
        void (async () => {
          const transport = new StreamableHTTPServerTransport({
            sessionIdGenerator: undefined,
          });
          const mcp = makeMcpServer(exposeEcho);
          res.on("close", () => {
            void transport.close();
            void mcp.close();
          });
          await mcp.connect(transport);
          const body = Buffer.concat(chunks).toString("utf8");
          await transport.handleRequest(
            req,
            res,
            body === "" ? undefined : JSON.parse(body),
          );
        })().catch(() => {
          if (!res.headersSent) res.writeHead(400).end("bad request");
        });
      });
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    if (typeof address !== "object" || address === null) {
      throw new Error("MCP server did not bind a port");
    }
    mcpUrl = `http://127.0.0.1:${address.port}/mcp`;
  });

  afterEach(async () => {
    await facade?.close();
    facade = undefined;
    vi.restoreAllMocks();
    if (server)
      await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  async function createConfiguredFacade() {
    const configKey = "octop.webskill.runtime.42";
    localStorage.setItem(
      configKey,
      JSON.stringify({
        mcp: {
          endpoints: [
            {
              name: "custom-e2e",
              url: mcpUrl,
              transport: "streamable-http",
              enabled: true,
              allowHttp: true,
              allowPrivateHosts: true,
            },
          ],
        },
      }),
    );
    const { createLocalStorageRuntimeConfigStore } = await import(
      "@webskill/chatbot"
    );
    const initialConfig = await createLocalStorageRuntimeConfigStore({
      key: configKey,
    }).load();
    facade = createHostConnectFacade(
      createPageActionConsent({
        userId: 42,
        origin: () => undefined,
        locale: () => "en",
      }),
      createDownloadConsent({ userId: 42, locale: () => "en" }),
      { configKey, initialConfig, userId: 42 },
    );
    return { facade, configKey };
  }

  it("loads config endpoints and lets the model call them, with lifecycle preserved", async () => {
    const warnings = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    const { facade, configKey } = await createConfiguredFacade();

    const endpoints = await facade.listEndpoints();
    expect(endpoints.map((endpoint) => endpoint.config.name)).toEqual([
      "octop-webskill",
      "custom-e2e",
    ]);
    await expect
      .poll(async () => (await facade.listEndpoints())[1]?.status)
      .toBe("connected");
    expect(endpoints[1]).toMatchObject({ source: "custom" });
    expect((await facade.testEndpoint("custom-e2e")).ok).toBe(true);

    const specs = await facade.toolSource.listToolSpecs();
    expect(specs.map((spec) => spec.name)).toContain("custom-e2e__custom_echo");
    expect(warnings).not.toHaveBeenCalled();
    const result = await facade.toolSource.call("custom-e2e__custom_echo", {});
    expect(JSON.stringify(result)).toContain("custom-service-called");

    await facade.setEndpointEnabled!("custom-e2e", false);
    expect(
      (await facade.toolSource.listToolSpecs()).map((spec) => spec.name),
    ).not.toContain("custom-e2e__custom_echo");
    await facade.setEndpointEnabled!("custom-e2e", true);
    expect(
      (await facade.toolSource.listToolSpecs()).map((spec) => spec.name),
    ).toContain("custom-e2e__custom_echo");

    await facade.updateEndpoint!("custom-e2e", {
      name: "custom-e2e",
      url: `${mcpUrl}?version=2`,
      transport: "streamable-http",
      allowHttp: true,
      allowPrivateHosts: true,
    });
    const updated = await facade.listEndpoints();
    expect(updated[1]?.config.url).toContain("?version=2");
    expect(updated[1]?.status).toBe("connected");

    await facade.removeEndpoint("custom-e2e");
    expect(
      (await facade.listEndpoints()).map((endpoint) => endpoint.config.name),
    ).toEqual(["octop-webskill"]);
    expect(
      JSON.parse(localStorage.getItem(configKey) ?? "{}").mcp.endpoints,
    ).toEqual([]);
  });

  it("does not hide a real remote MCP listing failure", async () => {
    exposeEcho = false;
    const warnings = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    const { facade } = await createConfiguredFacade();
    await expect
      .poll(async () => (await facade.listEndpoints())[1]?.status)
      .toBe("connected");

    await facade.toolSource.listToolSpecs();

    expect(warnings).toHaveBeenCalledWith(
      expect.stringContaining('MCP endpoint "custom-e2e" is unavailable'),
    );
    expect(warnings).toHaveBeenCalledWith(
      expect.stringContaining("MCP error -32601"),
    );
    expect(
      warnings.mock.calls
        .flat()
        .filter((message) => message.includes("octop-webskill")),
    ).toEqual([]);
  });
});
