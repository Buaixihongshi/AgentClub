import { useCallback, useEffect, useRef, useState } from "react";
import { fetchObservabilityOverview } from "../lib/api";
import type { ObservabilityOverviewPayload } from "../types";

export type ObservabilityWindow = "today" | "7d" | "30d" | "all";

/** Global observability payload for one time window. Latest request wins. */
export function useObservabilityOverview(window: ObservabilityWindow) {
  const [data, setData] = useState<ObservabilityOverviewPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    const request = ++latest.current;
    setLoading(true);
    fetchObservabilityOverview(window)
      .then((payload) => {
        if (request !== latest.current) return;
        setData(payload);
        setError(null);
      })
      .catch((err: unknown) => {
        if (request !== latest.current) return;
        setError(err instanceof Error ? err.message : "加载观测数据失败");
      })
      .finally(() => {
        if (request === latest.current) setLoading(false);
      });
  }, [window, tick]);

  const refresh = useCallback(() => setTick((value) => value + 1), []);
  return { data, loading, error, refresh };
}
