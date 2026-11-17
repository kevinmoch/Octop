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
  const generationRef = useRef(0);

  const ensureSession = useCallback(
    async (expiredSessionId?: string): Promise<string | null> => {
      if (!enabled) return null;
      if (expiredSessionId) {
        const current = sessionRef.current;
        if (current && current !== expiredSessionId) return current;
        if (current === expiredSessionId) {
          generationRef.current += 1;
          creatingRef.current = null;
          sessionRef.current = null;
          setSessionId(null);
        }
      }
      if (sessionRef.current) return sessionRef.current;
      if (!userId) throw new Error("Octop user identity is not ready");
      if (!creatingRef.current) {
        const generation = generationRef.current;
        const creation = browserAgentApi
          .createSession()
          .then(async (created) => {
            if (generation !== generationRef.current) {
              await browserAgentApi
                .deleteSession(created.sessionId)
                .catch(() => undefined);
              throw new Error(
                "The WebSkill session was closed before creation completed",
              );
            }
            sessionRef.current = created.sessionId;
            setSessionId(created.sessionId);
            return created.sessionId;
          })
          .finally(() => {
            if (creatingRef.current === creation) creatingRef.current = null;
          });
        creatingRef.current = creation;
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
      const renewed = await ensureSession(current);
      if (!renewed) throw new Error("The WebSkill session is not available");
      return browserAgentApi.issueTicket(renewed);
    }
  }, [ensureSession]);

  const renewSession = useCallback(
    async (expectedSessionId: string) => {
      await ensureSession(expectedSessionId);
    },
    [ensureSession],
  );

  const closeSession = useCallback(() => {
    generationRef.current += 1;
    creatingRef.current = null;
    const id = sessionRef.current;
    sessionRef.current = null;
    setSessionId(null);
    if (enabled && id)
      void browserAgentApi.deleteSession(id).catch(() => undefined);
  }, [enabled]);

  useEffect(
    () => () => {
      generationRef.current += 1;
      creatingRef.current = null;
      const id = sessionRef.current;
      sessionRef.current = null;
      if (enabled && id)
        void browserAgentApi.deleteSession(id).catch(() => undefined);
    },
    [enabled],
  );

  return { sessionId, issueTicket, renewSession, closeSession };
}
