import { ipcRenderer } from "electron";
import { createPageAgentHandler } from "@webskill/sdk/browser";

/**
 * Preload injected into every local browser tab.
 *
 * It owns the real WebSkill PageAgentHandler for the page and exposes a single
 * IPC endpoint to the main process. No Node.js capability is exposed to the
 * page itself: `contextIsolation` keeps this world private, and the page sees
 * only the DOM side effects of perception and actions.
 *
 * The channels are literals, not `IPC_CHANNELS` imports: the Dashboard preload
 * is sandboxed and cannot `require()` a shared chunk, and importing the contract
 * from two preload entries makes Rollup hoist it into one. Keep these in sync
 * with `shared/ipc-contract.ts`.
 */
const FRAME_REQUEST_CHANNEL = "octop:browser:frame:request";
const FRAME_RESPONSE_CHANNEL = "octop:browser:frame:response";
const FRAME_PICK_FILES_CHANNEL = "octop:browser:frame:pick-files";

interface PickedFilePayload {
  name: string;
  type: string;
  /** Base64 file contents. */
  data: string;
}

function decodeBase64(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

const handler = createPageAgentHandler({
  actionScope: { include: ["body"] },
  document,
  // Nested-frame discovery is opt-in in the SDK; the browser-extension example
  // leaves it off, so the main frame is the only target here.
  discoverNestedFrames: false,
  // Page `attach` actions need real File objects. The main process shows the
  // native picker (parented to the Dashboard window) and sends the bytes back.
  pickFiles: async () => {
    const picked = (await ipcRenderer.invoke(FRAME_PICK_FILES_CHANNEL)) as
      | PickedFilePayload[]
      | undefined;
    if (!picked || picked.length === 0) return undefined;
    return picked.map(
      (file) =>
        new File([decodeBase64(file.data)], file.name, {
          type: file.type,
        }),
    );
  },
});

ipcRenderer.on(
  FRAME_REQUEST_CHANNEL,
  async (_event, msg: { requestId?: string; request?: unknown }) => {
    const requestId = String(msg?.requestId ?? "");
    if (!requestId) return;
    try {
      const reply = await handler.handle(msg?.request as never);
      ipcRenderer.send(FRAME_RESPONSE_CHANNEL, { requestId, ok: true, reply });
    } catch (error) {
      ipcRenderer.send(FRAME_RESPONSE_CHANNEL, {
        requestId,
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  },
);
