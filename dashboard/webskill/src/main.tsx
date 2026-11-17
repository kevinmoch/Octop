import { createRoot } from "react-dom/client";
import "@webskill/chatbot/chatbot.css";
import "@webskill/console/console.css";
import App from "./App";
import { OctopHostBridge } from "./bridge";
import { createWebSkillHost } from "./host";
import { assertSecureRuntime } from "./security";
import "./styles.css";

async function bootstrap() {
  const bridge = new OctopHostBridge();
  try {
    await assertSecureRuntime();
    const init = await bridge.ready();
    const host = await createWebSkillHost(init, bridge);
    bridge.onAppearance(({ locale, theme }) => {
      void host.runtimeConfig.load().then((config) => {
        if (
          config.appearance.locale === locale &&
          config.appearance.theme === theme
        )
          return;
        return host.runtimeConfig.save({
          ...config,
          appearance: { ...config.appearance, locale, theme },
        });
      });
    });
    createRoot(document.getElementById("root")!).render(
      <App host={host} init={init} />,
    );
  } catch (error) {
    bridge.dispose();
    const zh = navigator.language.toLowerCase().startsWith("zh");
    createRoot(document.getElementById("root")!).render(
      <main className="runtime-error" role="alert">
        <h1>{zh ? "WebSkill 无法启动" : "WebSkill cannot start"}</h1>
        <p>
          {zh
            ? "请通过 HTTPS 或 localhost 访问，并启用 OPFS 与 WebCrypto。不会回退到不安全的存储。"
            : "Use HTTPS or localhost and enable OPFS and WebCrypto. Insecure storage is not used as a fallback."}
        </p>
        <pre>
          {error instanceof Error
            ? error.message
            : "Runtime initialization failed"}
        </pre>
      </main>,
    );
  }
}

void bootstrap();
