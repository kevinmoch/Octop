import { useEffect, useState } from "react";
import type {
  RuntimeAppearanceConfig,
  RuntimeConfigStore,
} from "@webskill/chatbot";

/** The host subscribes to the same public store consumed by Chatbot and Console. */
export function useHostAppearance(
  store: RuntimeConfigStore,
  fallback: RuntimeAppearanceConfig,
): RuntimeAppearanceConfig {
  const [appearance, setAppearance] = useState(fallback);
  useEffect(() => {
    let live = true;
    const refresh = () => {
      void store.load().then((config) => {
        if (live) setAppearance(config.appearance);
      });
    };
    refresh();
    const unsubscribe = store.subscribe?.(refresh);
    return () => {
      live = false;
      unsubscribe?.();
    };
  }, [store]);
  return appearance;
}
