import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { IS_MOCK } from "../api";
import type { Role } from "../api/types";

/** Live telemetry arrives once a minute and the API stage allows 1 req/s for everyone, so live polls 5x slower. */
export const POLL_SCALE = IS_MOCK ? 1 : 5;

/**
 * Re-runs `load` every `intervalMs` × POLL_SCALE (and when deps change). Keeps the last good value on error.
 * Ticks are skipped while the tab is hidden or the previous tick is still in flight.
 */
export function usePolling<T>(load: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    let inFlight = false;
    const tick = async () => {
      if (inFlight || document.hidden) return;
      inFlight = true;
      try {
        await refresh();
      } finally {
        inFlight = false;
      }
    };
    const onVisible = () => {
      if (!document.hidden) tick();
    };
    tick();
    const id = setInterval(tick, intervalMs * POLL_SCALE);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs, refresh, ...deps]);

  return { data, error, refresh };
}

export const RoleContext = createContext<{ role: Role; setRole: (r: Role) => void }>({
  role: "MAINT_LEAD",
  setRole: () => undefined,
});

export const useRole = () => useContext(RoleContext);
