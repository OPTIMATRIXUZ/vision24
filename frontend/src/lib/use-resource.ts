"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export function useResource<T>(
  load: () => Promise<T>,
  { pollMs, deps = [] }: { pollMs?: number; deps?: unknown[] } = {},
): { data: T | null; error: unknown; loading: boolean; refresh: () => Promise<void> } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);

  const generation = useRef(0);

  const loadRef = useRef(load);

  const refresh = useCallback(async () => {
    const mine = generation.current;
    try {
      const next = await loadRef.current();
      if (generation.current !== mine) return;
      setData(next);
      setError(null);
    } catch (e) {
      if (generation.current !== mine) return;
      setError(e);
    }
  }, []);

  useEffect(() => {
    loadRef.current = load;
    generation.current += 1;
    refresh();
    if (!pollMs) return;
    const id = setInterval(refresh, pollMs);
    return () => {
      generation.current += 1;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, pollMs, ...deps]);

  const loading = data === null && error === null;

  return { data, error, loading, refresh };
}
