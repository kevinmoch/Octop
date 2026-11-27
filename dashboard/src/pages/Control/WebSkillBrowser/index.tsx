import { Button, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { useDesktopBrowserMode } from "../../../hooks/useDesktopBrowserMode";
import { isDesktopShell } from "../../../utils/desktopBridge";
import LocalBrowserPage from "../LocalBrowser";
import RemoteBrowserPage from "../RemoteBrowser";

/**
 * Control → Browser.
 *
 * Desktop defaults to real local tabs; the streaming viewer stays available as
 * a persisted fallback (and via `OCTOP_DESKTOP_BROWSER_MODE=remote`). Server
 * mode is unchanged.
 */
export default function WebSkillBrowserPage() {
  const { t } = useTranslation();
  const { mode, ready, setMode } = useDesktopBrowserMode();

  if (!isDesktopShell()) {
    return <RemoteBrowserPage embedded enhanced />;
  }
  if (!ready) {
    return (
      <div style={{ height: "100%", display: "grid", placeItems: "center" }}>
        <Spin />
      </div>
    );
  }
  if (mode === "remote") {
    return (
      <RemoteBrowserPage
        embedded
        enhanced
        addressBarExtraEnd={
          <Button size="small" onClick={() => void setMode("local")}>
            {t("localBrowser.useLocal")}
          </Button>
        }
      />
    );
  }
  return <LocalBrowserPage onUseRemote={() => void setMode("remote")} />;
}
