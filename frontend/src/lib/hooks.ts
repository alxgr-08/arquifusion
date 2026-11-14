import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Role } from "../api/types";

/** Re-runs `load` every `intervalMs` (and when deps change). Keeps the last good value on error. */
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
    refresh();
    const id = setInterval(refresh, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, refresh, ...deps]);

  return { data, error, refresh };
}

export const RoleContext = createContext<{ role: Role; setRole: (r: Role) => void }>({
  role: "MAINT_LEAD",
  setRole: () => undefined,
});

export const useRole = () => useContext(RoleContext);
