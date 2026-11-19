import type { ChatbotConfig } from "@webskill/chatbot";
import {
  BrowserWorkerScriptExecutor,
  WORKER_BOOTSTRAP_SOURCE,
  createIframeWorker,
} from "@webskill/sdk/browser";

export const createSandboxExecutor: NonNullable<
  ChatbotConfig["executorFactory"]
> = (deps) =>
  new BrowserWorkerScriptExecutor({
    ...deps,
    workerFactory: () =>
      createIframeWorker(WORKER_BOOTSTRAP_SOURCE, {
        documentUrl: "/webskill/sandbox/",
      }),
  });
