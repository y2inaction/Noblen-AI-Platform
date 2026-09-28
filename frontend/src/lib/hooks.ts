"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/client";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** GET `path` (skipped when null) and re-fetch when it changes or on reload().
 *  Data from the previous request stays visible while the next one loads. */
export function useApi<T>(path: string | null): Loaded<T> {
  const [tick, setTick] = useState(0);
  const key = path === null ? null : `${tick}:${path}`;
  const [result, setResult] = useState<{ key: string | null; data: T | null; error: string | null }>({
    key: null,
    data: null,
    error: null,
  });

  useEffect(() => {
    if (key === null || path === null) return;
    const controller = new AbortController();
    api<T>(path, { signal: controller.signal })
      .then((value) => setResult({ key, data: value, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : "Something went wrong.";
        setResult((prev) => ({ key, data: prev.data, error: message }));
      });
    return () => controller.abort();
  }, [key, path]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const current = key !== null && result.key === key;
  return {
    data: result.data,
    error: current ? result.error : null,
    loading: key !== null && !current,
    reload,
  };
}

/** Run a mutation, tracking busy/error state. Returns the result or null on error. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | null> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      return null;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}
