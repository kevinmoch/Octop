import { useCallback, useEffect, useRef, useState } from "react";
import {
  browserAgentApi,
  isBrowserAgentSessionMissing,
} from "../../../api/modules/browserAgent";

export function useWebSkillAgentSession(
  enabled: boolean,
  userId: number | undefined,
) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const creatingRef = useRef<Promise<string> | null>(null);

  const ensureSession = useCallback(
    async (renew = false): Promise<string | null> => {
      if (!enabled) return null;
      if (renew) {
        sessionRef.current = null;
        setSessionId(null);
      }
      if (sessionRef.current) return sessionRef.current;
      if (!userId) throw new Error("Octop user identity is not ready");
      if (!creatingRef.current) {
        creatingRef.current = browserAgentApi
          .createSession()
          .then((created) => {
            sessionRef.current = created.sessionId;
            setSessionId(created.sessionId);
            return created.sessionId;
          })
          .finally(() => {
            creatingRef.current = null;
          });
      }
      return creatingRef.current;
    },
    [enabled, userId],
  );

  const issueTicket = useCallback(async () => {
    const current = await ensureSession();
    if (!current) throw new Error("The WebSkill session is not available");
    try {
      return await browserAgentApi.issueTicket(current);
    } catch (error) {
      if (!isBrowserAgentSessionMissing(error)) throw error;
      const renewed = await ensureSession(true);
      if (!renewed) throw new Error("The WebSkill session is not available");
      return browserAgentApi.issueTicket(renewed);
    }
  }, [ensureSession]);

  const renewSession = useCallback(async () => {
    await ensureSession(true);
  }, [ensureSession]);

  const closeSession = useCallback(() => {
    const id = sessionRef.current;
    sessionRef.current = null;
    setSessionId(null);
    if (enabled && id)
      void browserAgentApi.deleteSession(id).catch(() => undefined);
  }, [enabled]);

  useEffect(
    () => () => {
      const id = sessionRef.current;
      if (enabled && id)
        void browserAgentApi.deleteSession(id).catch(() => undefined);
    },
    [enabled],
  );

  return { sessionId, issueTicket, renewSession, closeSession };
}
